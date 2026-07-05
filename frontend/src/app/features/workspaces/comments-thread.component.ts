import { Component, Input, OnChanges, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { Comment, CommentService } from '../../core/services/comment.service';
import { AuthService } from '../../core/services/auth.service';
import { NotificationService } from '../../core/services/notification.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

interface ThreadNode extends Comment {
  replies: ThreadNode[];
}

/**
 * Phase 20.b — threaded comments panel for a shared conversation.
 *
 * Renders a conversation's comments as nested threads and lets the current user
 * post top-level comments, reply, and edit/delete their own. Anchoring a comment
 * to a specific message is supported by the API (message_id) but the panel here
 * shows conversation-level threads; message-level anchoring can be wired from
 * the chat transcript by passing a messageId to CommentService.create.
 */
@Component({
  selector: 'app-comments-thread',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe],
  templateUrl: './comments-thread.component.html',
  styleUrls: ['./comments-thread.component.css'],
})
export class CommentsThreadComponent implements OnChanges {
  @Input({ required: true }) conversationId!: string;

  private readonly api = inject(CommentService);
  private readonly auth = inject(AuthService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);

  readonly comments = signal<Comment[]>([]);
  readonly loading = signal(false);
  readonly replyingTo = signal<string | null>(null);
  readonly editingId = signal<string | null>(null);

  newComment = '';
  replyBody = '';
  editBody = '';

  readonly myId = computed(() => this.auth.currentUser()?.id ?? '');

  /** Build the nested thread tree from the flat, chronologically-ordered list. */
  readonly tree = computed<ThreadNode[]>(() => {
    const nodes = new Map<string, ThreadNode>();
    for (const c of this.comments()) nodes.set(c.id, { ...c, replies: [] });
    const roots: ThreadNode[] = [];
    for (const node of nodes.values()) {
      if (node.parent_id && nodes.has(node.parent_id)) {
        nodes.get(node.parent_id)!.replies.push(node);
      } else {
        roots.push(node);
      }
    }
    return roots;
  });

  ngOnChanges(): void {
    if (this.conversationId) this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.list(this.conversationId).subscribe({
      next: (list) => { this.comments.set(list); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  post(): void {
    const body = this.newComment.trim();
    if (!body) return;
    this.api.create(this.conversationId, body).subscribe({
      next: (c) => { this.comments.update((l) => [...l, c]); this.newComment = ''; },
      error: () => this.notify.add('error', this.i18n.translate('cmt.postFailed')),
    });
  }

  startReply(id: string): void {
    this.replyingTo.set(id);
    this.replyBody = '';
  }

  submitReply(parentId: string): void {
    const body = this.replyBody.trim();
    if (!body) return;
    this.api.create(this.conversationId, body, { parentId }).subscribe({
      next: (c) => {
        this.comments.update((l) => [...l, c]);
        this.replyingTo.set(null);
        this.replyBody = '';
      },
      error: () => this.notify.add('error', this.i18n.translate('cmt.replyFailed')),
    });
  }

  startEdit(c: Comment): void {
    this.editingId.set(c.id);
    this.editBody = c.body;
  }

  submitEdit(c: Comment): void {
    const body = this.editBody.trim();
    if (!body) return;
    this.api.update(this.conversationId, c.id, body).subscribe({
      next: (updated) => {
        this.comments.update((l) => l.map((x) => (x.id === updated.id ? updated : x)));
        this.editingId.set(null);
      },
      error: () => this.notify.add('error', this.i18n.translate('cmt.editFailed')),
    });
  }

  remove(c: Comment): void {
    if (!confirm(this.i18n.translate('cmt.deleteConfirm'))) return;
    this.api.delete(this.conversationId, c.id).subscribe({
      next: () =>
        this.comments.update((l) =>
          l.map((x) => (x.id === c.id ? { ...x, deleted: true, body: '' } : x)),
        ),
      error: () => this.notify.add('error', this.i18n.translate('ws.deleteFailed')),
    });
  }

  cancelInline(): void {
    this.replyingTo.set(null);
    this.editingId.set(null);
  }
}
