import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { ChatMessage, Conversation, ConversationSummary } from '../models/chat.models';
import { AppConfigService } from '../config/app-config.service';

@Injectable({ providedIn: 'root' })
export class ConversationService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/conversations`;
  }

  list(profileId: string): Observable<ConversationSummary[]> {
    return this.http.get<ConversationSummary[]>(this.baseUrl, {
      params: { profile_id: profileId },
    });
  }

  create(title: string, model: string, profileId: string): Observable<ConversationSummary> {
    return this.http.post<ConversationSummary>(this.baseUrl, { title, model, profile_id: profileId });
  }

  get(id: string): Observable<Conversation> {
    return this.http.get<Conversation>(`${this.baseUrl}/${id}`);
  }

  rename(id: string, title: string): Observable<ConversationSummary> {
    return this.http.patch<ConversationSummary>(`${this.baseUrl}/${id}`, { title });
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }

  appendMessages(id: string, messages: ChatMessage[]): Observable<void> {
    return this.http.post<void>(`${this.baseUrl}/${id}/messages`, { messages });
  }
}
