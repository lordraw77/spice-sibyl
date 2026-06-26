import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { PromptTemplate } from '../models/chat.models';
import { AppConfigService } from '../config/app-config.service';

@Injectable({ providedIn: 'root' })
export class TemplateService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/templates`;
  }

  list(profileId: string): Observable<PromptTemplate[]> {
    return this.http.get<PromptTemplate[]>(this.baseUrl, {
      params: { profile_id: profileId },
    });
  }

  create(name: string, content: string, profileId: string): Observable<PromptTemplate> {
    return this.http.post<PromptTemplate>(this.baseUrl, { name, content, profile_id: profileId });
  }

  update(id: string, data: { name?: string; content?: string }): Observable<PromptTemplate> {
    return this.http.patch<PromptTemplate>(`${this.baseUrl}/${id}`, data);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }
}
