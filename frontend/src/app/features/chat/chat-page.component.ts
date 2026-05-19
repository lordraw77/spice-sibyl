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
  OnInit,
  ViewChild,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ChatService } from '../../core/services/chat.service';
import { ChatCompletionResponse, ChatMessage, ChatModel, ProviderSummary } from '../../core/models/chat.models';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { NotificationService } from '../../core/services/notification.service';

@Component({
  selector: 'app-chat-page',
  standalone: true,
  imports: [CommonModule, FormsModule, DatePipe],
  templateUrl: './chat-page.component.html',
  styleUrl: './chat-page.component.css',
})
export class ChatPageComponent implements OnInit, AfterViewChecked {
  private readonly chatService = inject(ChatService);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly notifications = inject(NotificationService);
  readonly availabilityFilter = signal<'all' | 'free'>('all');

  @ViewChild('messagesContainer')
  private messagesContainer?: ElementRef<HTMLDivElement>;

  readonly messages = signal<ChatMessage[]>([
    {
      role: 'assistant',
      content: 'Welcome to SpiceSibyl. Select a model and start chatting.',
      model: 'SpiceSibyl',
      created_at: Math.floor(Date.now() / 1000),
    },
  ]);
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
  loading = false;
  streaming = false;
  sidebarOpen = false;

  toggleSidebar(): void {
    this.sidebarOpen = !this.sidebarOpen;
  }
  /** When true, the view scrolls to the bottom on the next AfterViewChecked cycle. */
  private shouldAutoScroll = true;

  setAvailabilityFilter(value: 'all' | 'free'): void {
    this.availabilityFilter.set(value);
    this.ensureValidSelectedModel();
  }
  ngOnInit(): void {
    // Enable GitHub-Flavoured Markdown with soft line breaks
    marked.setOptions({ breaks: true, gfm: true });

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

    this.chatService.stream({
      model: this.model,
      messages: userMessages,
      stream: true,
      temperature: 0.7,
    }).subscribe({
      next: ({ event, data }) => {
        if (event === 'done') {
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
      },
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
    if (message.role !== 'assistant') {
      return this.sanitizer.bypassSecurityTrustHtml(this.escapeHtml(message.content).replace(/\n/g, '<br>'));
    }
    const html = marked.parse(message.content) as string;
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