import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked, Renderer } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js';

import { ConversationService } from '../../core/services/conversation.service';
import { Conversation, ChatMessage } from '../../core/models/chat.models';

@Component({
  selector: 'app-shared-view',
  standalone: true,
  imports: [CommonModule, DatePipe],
  template: `
    <div class="shared-shell">
      <header class="shared-header">
        <div class="shared-brand">SpiceSibyl</div>
        <div class="shared-badge">Conversazione condivisa</div>
      </header>

      <div class="shared-loading" *ngIf="loading()">
        <span class="dot-spin"></span>
      </div>

      <div class="shared-error" *ngIf="error()">
        <h2>Link non valido</h2>
        <p>Questa conversazione condivisa non esiste o è stata rimossa.</p>
      </div>

      <ng-container *ngIf="conversation() as conv">
        <div class="shared-meta">
          <h1 class="shared-title">{{ conv.title }}</h1>
          <span class="shared-info">{{ conv.model }} · {{ (conv.created_at * 1000) | date:'dd/MM/yyyy HH:mm' }}</span>
        </div>

        <div class="shared-messages">
          <div
            class="message"
            *ngFor="let message of conv.messages"
            [class.user]="message.role === 'user'"
          >
            <div class="role">{{ roleLabel(message) }}</div>
            <div class="bubble markdown-body" [innerHTML]="renderedContent(message)"></div>
            <div class="meta" *ngIf="message.role === 'assistant' && (message.provider || message.latency_ms)">
              <span *ngIf="message.provider">{{ message.provider }}</span>
              <span *ngIf="message.latency_ms"> · {{ (message.latency_ms / 1000).toFixed(2) }}s</span>
              <span *ngIf="message.total_tokens"> · {{ message.total_tokens }} tok</span>
            </div>
          </div>
        </div>
      </ng-container>
    </div>
  `,
  styles: [`
    .shared-shell {
      max-width: 800px;
      margin: 0 auto;
      padding: 2rem 1.5rem;
      min-height: 100vh;
      background: var(--bg-primary);
      color: var(--text-primary);
    }
    .shared-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--border);
      margin-bottom: 1.5rem;
    }
    .shared-brand {
      font-size: 1.1rem;
      font-weight: 700;
      color: var(--text-primary);
    }
    .shared-badge {
      font-size: .75rem;
      padding: .25rem .6rem;
      border-radius: .4rem;
      background: var(--accent-bg);
      color: var(--accent);
      border: 1px solid var(--accent-border);
    }
    .shared-loading {
      display: flex;
      justify-content: center;
      padding: 3rem;
    }
    .shared-error {
      text-align: center;
      padding: 3rem;
      color: var(--text-muted);
    }
    .shared-error h2 {
      color: var(--error);
      margin-bottom: .5rem;
    }
    .shared-meta {
      margin-bottom: 1.5rem;
    }
    .shared-title {
      font-size: 1.3rem;
      font-weight: 600;
      margin: 0 0 .35rem;
    }
    .shared-info {
      font-size: .78rem;
      color: var(--text-muted);
    }
    .shared-messages {
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .message {
      padding: .85rem 1rem;
      border-radius: .65rem;
      background: var(--bg-surface);
      border: 1px solid var(--border);
    }
    .message.user {
      background: var(--user-bubble);
      border-color: var(--user-border);
    }
    .role {
      font-size: .7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--text-muted);
      margin-bottom: .4rem;
    }
    .message.user .role { color: var(--user-role); }
    .bubble { font-size: .92rem; line-height: 1.6; }
    .meta {
      margin-top: .4rem;
      font-size: .7rem;
      color: var(--text-dim);
    }
    .dot-spin {
      width: 20px;
      height: 20px;
      border: 2px solid var(--border);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin .6s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    :host ::ng-deep .markdown-body pre {
      background: var(--code-bg);
      padding: .8rem;
      border-radius: .4rem;
      overflow-x: auto;
    }
    :host ::ng-deep .markdown-body code {
      font-family: 'JetBrains Mono', monospace;
      font-size: .85rem;
    }
    :host ::ng-deep .markdown-body p { margin: .4rem 0; }
  `]
})
export class SharedViewComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly conversationService = inject(ConversationService);
  private readonly sanitizer = inject(DomSanitizer);

  readonly conversation = signal<Conversation | null>(null);
  readonly loading = signal(true);
  readonly error = signal(false);

  ngOnInit(): void {
    const renderer = new Renderer();
    (renderer as unknown as Record<string, unknown>)['code'] =
      (code: string, lang: string | undefined): string => {
        const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext';
        const highlighted = hljs.highlight(code, { language }).value;
        return `<pre><code class="hljs language-${language}">${highlighted}</code></pre>`;
      };
    marked.use({ renderer, breaks: true, gfm: true });

    const token = this.route.snapshot.paramMap.get('token');
    if (!token) {
      this.loading.set(false);
      this.error.set(true);
      return;
    }

    this.conversationService.getShared(token).subscribe({
      next: (conv) => {
        this.conversation.set(conv);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.error.set(true);
      },
    });
  }

  roleLabel(message: ChatMessage): string {
    if (message.role === 'assistant') {
      return (message.model || 'assistant').replace(/^.*\//, '').toUpperCase();
    }
    return message.role;
  }

  renderedContent(message: ChatMessage): SafeHtml {
    const raw = message.content;
    const content = typeof raw === 'string' ? raw : (raw ?? '');
    if (message.role !== 'assistant') {
      return this.sanitizer.bypassSecurityTrustHtml(
        this.escapeHtml(content).replace(/\n/g, '<br>')
      );
    }
    const html = marked.parse(content, { async: false }) as string;
    const clean = DOMPurify.sanitize(html);
    return this.sanitizer.bypassSecurityTrustHtml(clean);
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
