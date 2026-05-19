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
    const nextMessages = [
      ...this.messages(),
      { role: 'user' as const, content: text, created_at: Math.floor(Date.now() / 1000) },
    ];
    this.messages.set(nextMessages);
    this.queueScrollToBottom();

    this.chatService.complete({
      model: this.model,
      messages: nextMessages,
      stream: false,
      temperature: 0.7,
    }).subscribe({
      next: (response) => {
        const reply = this.mapAssistantMessage(response);
        if (reply) {
          this.messages.update((items) => [...items, reply]);
          this.queueScrollToBottom();
        }
      },
      error: () => {
        this.messages.update((items) => [
          ...items,
          {
            role: 'assistant',
            content: 'Backend request failed.',
            model: this.model,
            created_at: Math.floor(Date.now() / 1000),
          },
        ]);
        this.queueScrollToBottom();
      },
      complete: () => {
        this.loading = false;
      },
    });

    this.prompt = '';
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