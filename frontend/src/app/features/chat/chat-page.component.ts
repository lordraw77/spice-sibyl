/**
 * ChatPageComponent — main chat interface.
 *
 * Responsibilities:
 *  - Load the model list from the backend and populate the sidebar selectors
 *  - Send user prompts via ChatService and render assistant replies as Markdown
 *  - Display per-message telemetry (latency, token counts, cost)
 *  - Manage auto-scroll: keeps the view pinned to the bottom unless the user
 *    scrolls up, at which point auto-scroll is paused until they return to
 *    within 80 px of the bottom
 *
 * XSS safety: all assistant HTML is parsed by marked then sanitized by
 * DOMPurify before being trusted via Angular's DomSanitizer.  User messages
 * are HTML-escaped and newlines are converted to <br> tags.
 */
import {
  AfterViewChecked,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { Subject, debounceTime, distinctUntilChanged, switchMap, of } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { CommonModule, DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { ChatService } from '../../core/services/chat.service';
import { ChatStateService } from '../../core/services/chat-state.service';
import { ConversationService } from '../../core/services/conversation.service';
import { ProfileService } from '../../core/services/profile.service';
import { ProfileModalComponent } from '../profile/profile-modal.component';
import { ChatCompletionResponse, ChatMessage, ChatModel, ConversationSummary, ProviderSummary, SearchResult, ToolDefinition, ToolEvent } from '../../core/models/chat.models';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { NotificationService } from '../../core/services/notification.service';

@Component({
  selector: 'app-chat-page',
  standalone: true,
  imports: [CommonModule, FormsModule, DatePipe, ProfileModalComponent],
  templateUrl: './chat-page.component.html',
  styleUrl: './chat-page.component.css',
})
export class ChatPageComponent implements OnInit, AfterViewChecked, OnDestroy {
  private readonly chatService = inject(ChatService);
  private readonly chatState = inject(ChatStateService);
  private readonly conversationService = inject(ConversationService);
  readonly profileService = inject(ProfileService);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly notifications = inject(NotificationService);
  private readonly router = inject(Router);
  readonly availabilityFilter = signal<'all' | 'free'>('all');
  readonly toolsEnabled = signal(false);
  readonly availableTools = signal<ToolDefinition[]>([]);

  constructor() {
    // Reload the conversation list whenever the active profile changes.
    // Reset messages only when the profile *actually* changes — not on every
    // component re-mount (e.g. the user navigated away and came back).
    // allowSignalWrites is required because newConversation() writes to this.messages.
    effect(() => {
      const profile = this.profileService.current();
      if (profile) {
        this.loadConversationList();
        if (profile.id !== this.chatState.lastActiveProfileId) {
          this.chatState.lastActiveProfileId = profile.id;
          this.currentConversationId = null;
          this.newConversation();
        }
      }
    }, { allowSignalWrites: true });
  }

  @ViewChild('messagesContainer')
  private messagesContainer?: ElementRef<HTMLDivElement>;

  readonly conversations = signal<ConversationSummary[]>([]);
  readonly searchResults = signal<SearchResult[]>([]);
  readonly searchQuery = signal('');
  readonly isSearching = signal(false);
  private readonly searchSubject = new Subject<string>();
  private readonly destroy$ = new Subject<void>();

  /** Show profile selector modal when no profile is active */
  readonly showProfileModal = computed(() => !this.profileService.current());

  // Alias to the singleton service signal so state survives navigation.
  readonly messages = this.chatState.messages;
  readonly models = signal<ChatModel[]>([]);
  readonly providers = signal<ProviderSummary[]>([]);
  readonly capabilityFilter = signal<string>('all');
  readonly selectedProviders = signal<string[]>([]);

  /** Sorted list of all capabilities present across loaded models, prefixed with 'all'. */
  readonly availableCapabilities = computed(() => {
    const caps = new Set<string>();
    for (const model of this.models()) {
      for (const capability of model.capabilities || []) {
        caps.add(capability);
      }
    }
    return ['all', ...Array.from(caps).sort()];
  });

  /** Models visible in the selector after applying provider, capability, and availability filters. */
  readonly filteredModels = computed(() => {
    const capability = this.capabilityFilter();
    const availability = this.availabilityFilter();
    const enabledProviders = new Set(this.selectedProviders());

    return this.models().filter((model) => {
      const providerOk =
        enabledProviders.size === 0 || enabledProviders.has(model.provider || '');

      const capabilityOk =
        capability === 'all' || (model.capabilities || []).includes(capability);

      const availabilityOk =
        availability === 'all' || !!model.free;

      return providerOk && capabilityOk && availabilityOk;
    });
  });

  prompt = '';
  model = 'ollama/qwen2.5:7b-instruct';
  sidebarOpen = window.innerWidth >= 992;

  // Delegated to ChatStateService so state survives navigation away and back.
  get loading(): boolean { return this.chatState.loading(); }
  set loading(v: boolean) { this.chatState.loading.set(v); }

  get streaming(): boolean { return this.chatState.streaming(); }
  set streaming(v: boolean) { this.chatState.streaming.set(v); }

  get currentConversationId(): string | null { return this.chatState.currentConversationId; }
  set currentConversationId(v: string | null) { this.chatState.currentConversationId = v; }

  toggleSidebar(): void {
    this.sidebarOpen = !this.sidebarOpen;
  }
  /** When true, the view scrolls to the bottom on the next AfterViewChecked cycle. */
  private shouldAutoScroll = true;

  toggleTools(): void {
    this.toolsEnabled.update(v => !v);
  }

  toolArgsSummary(args: Record<string, unknown> | undefined): string {
    if (!args || !Object.keys(args).length) return '';
    return Object.entries(args)
      .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
      .join(', ');
  }

  setAvailabilityFilter(value: 'all' | 'free'): void {
    this.availabilityFilter.set(value);
    this.ensureValidSelectedModel();
  }
  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  onSearchInput(value: string): void {
    this.searchQuery.set(value);
    this.searchSubject.next(value.trim());
  }

  clearSearch(): void {
    this.searchQuery.set('');
    this.searchResults.set([]);
    this.isSearching.set(false);
  }

  selectSearchResult(result: SearchResult): void {
    this.clearSearch();
    this.selectConversation(result.id);
  }

  ngOnInit(): void {
    marked.setOptions({ breaks: true, gfm: true });

    this.chatService.listTools().subscribe({
      next: tools => this.availableTools.set(tools),
      error: () => {},
    });

    this.searchSubject.pipe(
      debounceTime(300),
      distinctUntilChanged(),
      switchMap(q => {
        if (!q) { this.searchResults.set([]); this.isSearching.set(false); return of([]); }
        this.isSearching.set(true);
        return this.conversationService.search(q, this.profileService.currentId);
      }),
      takeUntil(this.destroy$),
    ).subscribe({
      next: results => { this.searchResults.set(results); this.isSearching.set(false); },
      error: () => { this.searchResults.set([]); this.isSearching.set(false); },
    });

    this.loadConversationList();

    this.chatService.models().subscribe({
      next: (response) => {
        this.models.set(response.data);
        this.providers.set(response.providers || []);
        // Pre-select all enabled providers so the model list is fully visible on load
        this.selectedProviders.set(
          (response.providers || [])
            .filter((provider) => provider.enabled)
            .map((provider) => provider.id)
        );

        // Keep the current model if it exists; otherwise fall back to the default / first
        const preferred = response.data.find((item) => item.id === this.model);
        if (!preferred && response.data.length > 0) {
          this.model = response.data.find((item) => item.default)?.id || response.data[0].id;
        }

        this.ensureValidSelectedModel();
        this.queueScrollToBottom(true);
      },
      error: () => {
        this.messages.update((items) => [
          ...items,
          {
            role: 'assistant',
            content: 'Could not load models from the backend.',
            model: 'system',
            created_at: Math.floor(Date.now() / 1000),
          },
        ]);
        this.queueScrollToBottom(true);
      },
    });
  }

  ngAfterViewChecked(): void {
    if (this.shouldAutoScroll) {
      this.scrollToBottom();
      this.shouldAutoScroll = false;
    }
  }

  onEnter(event: KeyboardEvent): void {
    if (!event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  send(): void {
    const text = this.prompt.trim();
    if (!text || this.loading) {
      return;
    }

    this.loading = true;
    this.prompt = '';

    const userMessages = [
      ...this.messages(),
      { role: 'user' as const, content: text, created_at: Math.floor(Date.now() / 1000) },
    ];

    // Add user message + empty assistant placeholder for streaming
    this.messages.set([
      ...userMessages,
      { role: 'assistant' as const, content: '', model: this.model, created_at: Math.floor(Date.now() / 1000) },
    ]);
    this.streaming = true;
    this.queueScrollToBottom();

    // Index of the assistant placeholder in the messages array
    const streamingIdx = userMessages.length;

    const tools = this.toolsEnabled() && this.availableTools().length
      ? this.availableTools()
      : undefined;

    this.chatService.stream({
      model: this.model,
      messages: userMessages.map(m => ({ ...m, tool_events: undefined })),
      stream: true,
      temperature: 0.7,
      tools,
    }).subscribe({
      next: ({ event, data }) => {
        if (event === 'done') {
          return;
        }

        // Tool call event — add to the assistant placeholder's tool_events
        if (event === 'tool_call') {
          const toolEvent: ToolEvent = {
            kind: 'call',
            id: data['id'] as string,
            name: data['name'] as string,
            arguments: data['arguments'] as Record<string, unknown>,
          };
          this.messages.update(items =>
            items.map((m, i) =>
              i === streamingIdx
                ? { ...m, tool_events: [...(m.tool_events ?? []), toolEvent] }
                : m
            )
          );
          return;
        }

        // Tool result event — append to tool_events
        if (event === 'tool_result') {
          const toolEvent: ToolEvent = {
            kind: 'result',
            id: data['id'] as string,
            name: data['name'] as string,
            result: data['result'] as string,
          };
          this.messages.update(items =>
            items.map((m, i) =>
              i === streamingIdx
                ? { ...m, tool_events: [...(m.tool_events ?? []), toolEvent] }
                : m
            )
          );
          return;
        }

        // Final meta chunk carries full telemetry — update the placeholder in place
        if (data['object'] === 'chat.completion.meta') {
          const choice = (data['choices'] as { message: Record<string, unknown> }[] | undefined)?.[0];
          const metaMsg = choice?.message;
          const metrics = data['metrics'] as Record<string, unknown> | undefined;
          const usage = data['usage'] as Record<string, unknown> | undefined;

          if (metaMsg) {
            this.messages.update(items =>
              items.map((m, i) =>
                i === streamingIdx
                  ? {
                      ...m,
                      provider: (metaMsg['provider'] ?? metrics?.['provider']) as string | undefined,
                      latency_ms: (metaMsg['latency_ms'] ?? metrics?.['latency_ms']) as number | undefined,
                      first_token_ms: (metaMsg['first_token_ms'] ?? metrics?.['first_token_ms']) as number | undefined,
                      prompt_tokens: (metaMsg['prompt_tokens'] ?? usage?.['prompt_tokens']) as number | undefined,
                      completion_tokens: (metaMsg['completion_tokens'] ?? usage?.['completion_tokens']) as number | undefined,
                      total_tokens: (metaMsg['total_tokens'] ?? usage?.['total_tokens']) as number | undefined,
                      tokens_per_second: (metaMsg['tokens_per_second'] ?? metrics?.['tokens_per_second']) as number | undefined,
                      finish_reason: metaMsg['finish_reason'] as string | undefined,
                      estimated_cost: metrics?.['estimated_cost'] as number | undefined,
                      created_at: (metaMsg['created_at'] ?? data['created']) as number | undefined,
                      capabilities: (metaMsg['capabilities'] as string[] | undefined) ?? [],
                      free: metaMsg['free'] as boolean | undefined,
                    }
                  : m
              )
            );
          }
          return;
        }

        // Regular streaming chunk — append delta content to the placeholder
        const delta = (data['choices'] as { delta?: { content?: string } }[] | undefined)?.[0]?.delta?.content;
        if (delta) {
          this.messages.update(items =>
            items.map((m, i) => i === streamingIdx ? { ...m, content: m.content + delta } : m)
          );
          this.queueScrollToBottom();
        }
      },
      error: (err: Error) => {
        const detail = err?.message || 'Backend request failed.';
        this.notifications.add('error', 'Chat request failed', detail);
        this.messages.update(items =>
          items.map((m, i) =>
            i === streamingIdx && !m.content
              ? { ...m, content: `⚠ ${detail}` }
              : m
          )
        );
        this.loading = false;
        this.streaming = false;
        this.queueScrollToBottom();
      },
      complete: () => {
        this.loading = false;
        this.streaming = false;
        this.queueScrollToBottom();
        this.persistExchange(userMessages[userMessages.length - 1], streamingIdx);
        if (!this.router.url.startsWith('/chat')) {
          this.notifications.add('success', 'Risposta ricevuta', 'Il modello ha terminato la risposta.', 6000, () => this.router.navigate(['/chat']));
        }
      },
    });
  }

  /**
   * After a successful exchange, create or update the persisted conversation.
   * Creates a new conversation on first send, then appends the user + assistant pair.
   */
  private persistExchange(userMessage: ChatMessage, assistantIdx: number): void {
    const assistantMessage = this.messages()[assistantIdx];
    if (!assistantMessage?.content) {
      return;
    }
    const saveMessages = () => {
      this.conversationService
        .appendMessages(this.currentConversationId!, [userMessage, assistantMessage])
        .subscribe({ next: () => this.loadConversationList() });
    };

    if (this.currentConversationId) {
      saveMessages();
    } else {
      const title = (typeof userMessage.content === 'string' ? userMessage.content : '').slice(0, 60);
      const profileId = this.profileService.currentId;
      this.conversationService.create(title, this.model, profileId).subscribe({
        next: (conv) => {
          this.currentConversationId = conv.id;
          saveMessages();
        },
      });
    }
  }

  /** Open the profile selector (clears current profile so the modal reappears). */
  switchProfile(): void {
    this.profileService.clear();
  }

  /** Load conversation list from backend for the active profile. */
  loadConversationList(): void {
    const profileId = this.profileService.currentId;
    this.conversationService.list(profileId).subscribe({
      next: (list) => this.conversations.set(list),
      error: () => {},
    });
  }

  /** Start a blank new chat without persisting anything until the first send. */
  newConversation(): void {
    this.currentConversationId = null;
    this.messages.set([
      {
        role: 'assistant',
        content: 'Nuova conversazione. Seleziona un modello e inizia a chattare.',
        model: 'SpiceSibyl',
        created_at: Math.floor(Date.now() / 1000),
      },
    ]);
    this.queueScrollToBottom(true);
    if (window.innerWidth < 992) {
      this.sidebarOpen = false;
    }
  }

  /** Load an existing conversation from the backend and display it. */
  selectConversation(id: string): void {
    if (id === this.currentConversationId) return;
    this.conversationService.get(id).subscribe({
      next: (conv) => {
        this.currentConversationId = conv.id;
        this.model = conv.model;
        this.messages.set(
          conv.messages.length ? conv.messages : [{
            role: 'assistant' as const,
            content: 'Nessun messaggio in questa conversazione.',
            model: 'SpiceSibyl',
            created_at: Math.floor(Date.now() / 1000),
          }]
        );
        this.queueScrollToBottom(true);
        // On mobile the sidebar is an overlay — close it so the chat is visible
        if (window.innerWidth < 992) {
          this.sidebarOpen = false;
        }
      },
      error: () => this.notifications.add('error', 'Errore', 'Impossibile caricare la conversazione.'),
    });
  }

  /** Delete a conversation and refresh the list. */
  deleteConversation(id: string, event: Event): void {
    event.stopPropagation();
    this.conversationService.delete(id).subscribe({
      next: () => {
        if (this.currentConversationId === id) {
          this.newConversation();
        }
        this.loadConversationList();
      },
      error: () => this.notifications.add('error', 'Errore', 'Impossibile eliminare la conversazione.'),
    });
  }

  /** Return the display label shown in the message header for a given turn. */
  roleLabel(message: ChatMessage): string {
    if (message.role === 'assistant') {
      // Strip the provider prefix (e.g. 'ollama/qwen2.5:7b' → 'QWEN2.5:7B')
      return (message.model || 'assistant').replace(/^.*\//, '').toUpperCase();
    }
    return message.role;
  }

  /** Return the ChatModel metadata for the currently selected model, if available. */
  selectedModelMeta(): ChatModel | undefined {
    return this.models().find((item) => item.id === this.model);
  }

  /**
   * Return sanitized HTML for rendering a message bubble.
   *
   * Assistant messages: Markdown → HTML via marked, then DOMPurify sanitization.
   * User messages: plain-text HTML escape with newline-to-<br> conversion.
   */
  renderedContent(message: ChatMessage): SafeHtml {
    const content = message.content ?? '';
    if (message.role !== 'assistant') {
      return this.sanitizer.bypassSecurityTrustHtml(this.escapeHtml(content).replace(/\n/g, '<br>'));
    }
    const html = marked.parse(content) as string;
    const clean = DOMPurify.sanitize(html);
    return this.sanitizer.bypassSecurityTrustHtml(clean);
  }

  setCapabilityFilter(value: string): void {
    this.capabilityFilter.set(value);
    this.ensureValidSelectedModel();
  }

  toggleProvider(providerId: string): void {
    const current = new Set(this.selectedProviders());

    if (current.has(providerId)) {
      current.delete(providerId);
    } else {
      current.add(providerId);
    }

    this.selectedProviders.set(Array.from(current));
    this.ensureValidSelectedModel();
  }

  isProviderSelected(providerId: string): boolean {
    return this.selectedProviders().includes(providerId);
  }

  /** Pause auto-scroll when the user scrolls more than 80 px above the bottom. */
  onMessagesScroll(): void {
    const container = this.messagesContainer?.nativeElement;
    if (!container) {
      return;
    }
    const threshold = 80;
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    this.shouldAutoScroll = distanceFromBottom <= threshold;
  }

  /** If the currently selected model is no longer in filteredModels, pick the first available one. */
  private ensureValidSelectedModel(): void {
    const filtered = this.filteredModels();
    if (!filtered.length) {
      this.model = '';
      return;
    }

    if (!filtered.find((item) => item.id === this.model)) {
      this.model = filtered[0].id;
    }
  }

  private queueScrollToBottom(force = false): void {
    if (force || this.shouldAutoScroll) {
      this.shouldAutoScroll = true;
    }
  }

  private scrollToBottom(): void {
    const container = this.messagesContainer?.nativeElement;
    if (!container) {
      return;
    }
    container.scrollTop = container.scrollHeight;
  }

  /** Map a raw API response to a ChatMessage with telemetry fields populated. */
  private mapAssistantMessage(response: ChatCompletionResponse): ChatMessage | null {
    const choice = response?.choices?.[0];
    const message = choice?.message;
    if (!message) {
      return null;
    }

    const modelMeta = this.models().find((item) => item.id === response.model);

    return {
      role: 'assistant',
      content: typeof message.content === 'string' ? message.content : JSON.stringify(message.content),
      model: response.model,
      provider: response.metrics?.provider,
      latency_ms: response.metrics?.latency_ms,
      first_token_ms: response.metrics?.first_token_ms,
      prompt_tokens: response.usage?.prompt_tokens,
      completion_tokens: response.usage?.completion_tokens,
      total_tokens: response.usage?.total_tokens,
      tokens_per_second: response.metrics?.tokens_per_second,
      finish_reason: choice?.finish_reason,
      estimated_cost: response.metrics?.estimated_cost,
      created_at: response.created,
      capabilities: modelMeta?.capabilities ?? [],
      free: modelMeta?.free,
    };
  }

  /** Escape special HTML characters to prevent XSS in user messages. */
  private escapeHtml(value: string): string {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
}