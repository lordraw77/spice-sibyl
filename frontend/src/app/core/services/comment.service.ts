/**
 * CommentService — HTTP client for Phase 20.b annotations & comments.
 *
 * Threaded comments on a conversation or a specific message within it. Anyone
 * who can access the conversation (owner or workspace member) may read/post;
 * editing/deleting is restricted server-side to the comment's author.
 */
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';

export interface Comment {
  id: string;
  conversation_id: string;
  message_id: string | null;
  parent_id: string | null;
  user_id: string;
  author_email: string;
  body: string;
  deleted: boolean;
  created_at: number;
  updated_at: number;
}

@Injectable({ providedIn: 'root' })
export class CommentService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private url(conversationId: string): string {
    return `${this.config.apiUrl}/conversations/${conversationId}/comments`;
  }

  list(conversationId: string): Observable<Comment[]> {
    return this.http.get<Comment[]>(this.url(conversationId));
  }

  create(
    conversationId: string,
    body: string,
    opts: { messageId?: string | null; parentId?: string | null } = {},
  ): Observable<Comment> {
    return this.http.post<Comment>(this.url(conversationId), {
      body,
      message_id: opts.messageId ?? null,
      parent_id: opts.parentId ?? null,
    });
  }

  update(conversationId: string, commentId: string, body: string): Observable<Comment> {
    return this.http.patch<Comment>(`${this.url(conversationId)}/${commentId}`, { body });
  }

  delete(conversationId: string, commentId: string): Observable<void> {
    return this.http.delete<void>(`${this.url(conversationId)}/${commentId}`);
  }
}
