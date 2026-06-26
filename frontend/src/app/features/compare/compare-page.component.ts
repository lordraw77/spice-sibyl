import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked, Renderer } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js';

import { ChatService } from '../../core/services/chat.service';
import { ChatModel, ProviderSummary } from '../../core/models/chat.models';

interface CompareSlot {
  model: string;
  content: string;
  streaming: boolean;
  done: boolean;
  latency_ms?: number;
  tokens_per_second?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  estimated_cost?: number;
  provider?: string;
  error?: string;
  subscription?: Subscription;
}

@Component({
  selector: 'app-compare-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './compare-page.component.html',
  styleUrl: './compare-page.component.css',
})
export class ComparePageComponent implements OnInit {
  private readonly chatService = inject(ChatService);
  private readonly sanitizer = inject(DomSanitizer);

  readonly models = signal<ChatModel[]>([]);
  readonly providers = signal<ProviderSummary[]>([]);

  selectedModels: string[] = ['', ''];
  prompt = '';
  loading = false;
  slots: CompareSlot[] = [];

  constructor() {
    const renderer = new Renderer();
    (renderer as unknown as Record<string, unknown>)['code'] =
      (code: string, lang: string | undefined): string => {
        const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext';
        const highlighted = hljs.highlight(code, { language }).value;
        return `<pre><code class="hljs language-${language}">${highlighted}</code></pre>`;
      };
    marked.use({ renderer, breaks: true, gfm: true });
  }

  ngOnInit(): void {
    this.chatService.models().subscribe({
      next: (resp) => {
        this.models.set(resp.data);
        this.providers.set(resp.providers || []);
        if (resp.data.length >= 2) {
          this.selectedModels = [resp.data[0].id, resp.data[1].id];
        }
      },
    });
  }

  addSlot(): void {
    if (this.selectedModels.length < 4) {
      this.selectedModels = [...this.selectedModels, ''];
    }
  }

  removeSlot(idx: number): void {
    if (this.selectedModels.length > 2) {
      this.selectedModels = this.selectedModels.filter((_, i) => i !== idx);
    }
  }

  trackByIndex(index: number): number { return index; }

  updateModel(idx: number, value: string): void {
    this.selectedModels = this.selectedModels.map((m, i) => i === idx ? value : m);
  }

  compare(): void {
    const text = this.prompt.trim();
    const activeModels = this.selectedModels.filter(m => m);
    if (!text || activeModels.length < 2 || this.loading) return;

    this.loading = true;
    this.slots = activeModels.map(model => ({
      model,
      content: '',
      streaming: true,
      done: false,
    }));

    const messages = [{ role: 'user' as const, content: text }];

    for (let i = 0; i < this.slots.length; i++) {
      const slot = this.slots[i];
      slot.subscription = this.chatService.stream({
        model: slot.model,
        messages,
        stream: true,
      }).subscribe({
        next: ({ event, data }) => {
          if (event === 'done') return;
          if (data['object'] === 'chat.completion.meta') {
            const metrics = data['metrics'] as Record<string, unknown> | undefined;
            const usage = data['usage'] as Record<string, unknown> | undefined;
            const choice = (data['choices'] as { message: Record<string, unknown> }[] | undefined)?.[0];
            const msg = choice?.message;
            slot.provider = (msg?.['provider'] ?? metrics?.['provider']) as string | undefined;
            slot.latency_ms = (msg?.['latency_ms'] ?? metrics?.['latency_ms']) as number | undefined;
            slot.tokens_per_second = (msg?.['tokens_per_second'] ?? metrics?.['tokens_per_second']) as number | undefined;
            slot.prompt_tokens = (msg?.['prompt_tokens'] ?? usage?.['prompt_tokens']) as number | undefined;
            slot.completion_tokens = (msg?.['completion_tokens'] ?? usage?.['completion_tokens']) as number | undefined;
            slot.estimated_cost = metrics?.['estimated_cost'] as number | undefined;
            return;
          }
          const delta = (data['choices'] as { delta?: { content?: string } }[] | undefined)?.[0]?.delta?.content;
          if (delta) slot.content += delta;
        },
        error: (err: Error) => {
          slot.error = err?.message || 'Request failed';
          slot.streaming = false;
          slot.done = true;
          this.checkAllDone();
        },
        complete: () => {
          slot.streaming = false;
          slot.done = true;
          this.checkAllDone();
        },
      });
    }
  }

  cancelAll(): void {
    for (const slot of this.slots) {
      slot.subscription?.unsubscribe();
      slot.streaming = false;
      slot.done = true;
    }
    this.loading = false;
  }

  private checkAllDone(): void {
    if (this.slots.every(s => s.done)) {
      this.loading = false;
    }
  }

  renderContent(content: string): SafeHtml {
    const html = marked.parse(content, { async: false }) as string;
    const clean = DOMPurify.sanitize(html);
    return this.sanitizer.bypassSecurityTrustHtml(clean);
  }

  modelLabel(id: string): string {
    const m = this.models().find(x => x.id === id);
    return (m?.label || id).replace(/^.*\//, '');
  }

  formatCost(v: number | undefined): string {
    if (v == null || v === 0) return '—';
    return '$' + v.toFixed(6);
  }
}
