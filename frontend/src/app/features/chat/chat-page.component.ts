import { Component, OnInit, computed, inject, signal } from '@angular/core';
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
  styleUrl: './chat-page.component.css'
})
export class ChatPageComponent implements OnInit {
  private readonly chatService = inject(ChatService);
  private readonly sanitizer = inject(DomSanitizer);

  readonly messages = signal<ChatMessage[]>([
    { role: 'assistant', content: 'Benvenuto in SpiceSibyl. Seleziona un modello e inizia a chattare.', model: 'SpiceSibyl', created_at: Math.floor(Date.now() / 1000) }
  ]);
  readonly models = signal<ChatModel[]>([]);
  readonly providers = signal<ProviderSummary[]>([]);
  readonly capabilityFilter = signal<string>('all');

  readonly availableCapabilities = computed(() => {
    const caps = new Set<string>();
    for (const model of this.models()) {
      for (const capability of model.capabilities || []) {
        caps.add(capability);
      }
    }
    return ['all', ...Array.from(caps).sort()];
  });

  readonly filteredModels = computed(() => {
    const filter = this.capabilityFilter();
    if (filter === 'all') {
      return this.models();
    }
    return this.models().filter((model) => (model.capabilities || []).includes(filter));
  });

  prompt = '';
  model = 'ollama/qwen2.5:7b-instruct';
  loading = false;

  ngOnInit(): void {
    marked.setOptions({ breaks: true, gfm: true });

    this.chatService.models().subscribe({
      next: (response) => {
        this.models.set(response.data);
        this.providers.set(response.providers || []);
        const preferred = response.data.find((item) => item.id === this.model);
        if (!preferred && response.data.length > 0) {
          this.model = response.data.find((item) => item.default) ?.id || response.data[0].id;
        }
      },
      error: () => {
        this.messages.update((items) => [
          ...items,
          { role: 'assistant', content: 'Impossibile caricare i modelli dal backend.', model: 'system', created_at: Math.floor(Date.now() / 1000) }
        ]);
      }
    });
  }

  send(): void {
    const text = this.prompt.trim();
    if (!text || this.loading) {
      return;
    }

    this.loading = true;
    const nextMessages = [...this.messages(), { role: 'user' as const, content: text, created_at: Math.floor(Date.now() / 1000) }];
    this.messages.set(nextMessages);

    this.chatService.complete({
      model: this.model,
      messages: nextMessages,
      stream: false,
      temperature: 0.7
    }).subscribe({
      next: (response) => {
        const reply = this.mapAssistantMessage(response);
        if (reply) {
          this.messages.update((items) => [...items, reply]);
        }
      },
      error: () => {
        this.messages.update((items) => [
          ...items,
          { role: 'assistant', content: 'Errore nella chiamata al backend.', model: this.model, created_at: Math.floor(Date.now() / 1000) }
        ]);
      },
      complete: () => {
        this.loading = false;
      }
    });

    this.prompt = '';
  }

  roleLabel(message: ChatMessage): string {
    if (message.role === 'assistant') {
      return (message.model || 'assistant').replace(/^.*\//, '').toUpperCase();
    }
    return message.role;
  }

  selectedModelMeta(): ChatModel | undefined {
    return this.models().find((item) => item.id === this.model);
  }

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
    const filtered = this.filteredModels();
    if (!filtered.find((item) => item.id === this.model) && filtered.length > 0) {
      this.model = filtered[0].id;
    }
  }

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
      free: modelMeta?.free
    };
  }

  private escapeHtml(value: string): string {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
}
