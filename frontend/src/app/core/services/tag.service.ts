import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { Tag } from '../models/chat.models';
import { AppConfigService } from '../config/app-config.service';

@Injectable({ providedIn: 'root' })
export class TagService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/tags`;
  }

  list(profileId: string): Observable<Tag[]> {
    return this.http.get<Tag[]>(this.baseUrl, {
      params: { profile_id: profileId },
    });
  }

  create(name: string, color: string, profileId: string): Observable<Tag> {
    return this.http.post<Tag>(this.baseUrl, { name, color, profile_id: profileId });
  }

  update(id: string, data: { name?: string; color?: string }): Observable<Tag> {
    return this.http.patch<Tag>(`${this.baseUrl}/${id}`, data);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }

  setConversationTags(conversationId: string, tagIds: string[]): Observable<void> {
    return this.http.put<void>(`${this.baseUrl}/conversations/${conversationId}`, { tag_ids: tagIds });
  }

  getConversationTags(conversationId: string): Observable<Tag[]> {
    return this.http.get<Tag[]>(`${this.baseUrl}/conversations/${conversationId}`);
  }
}
