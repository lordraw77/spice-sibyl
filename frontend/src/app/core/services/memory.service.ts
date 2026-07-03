/**
 * MemoryService — HTTP client for Phase 19 per-profile persistent memory.
 *
 * Wraps the /v1/memories endpoints: list / add / edit / toggle / delete
 * memories, forget-all, and the per-profile memory switch. The active profile
 * is conveyed via the X-Profile-ID header by the app's HTTP interceptor.
 */
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { ProfileMemory } from '../models/chat.models';
import { AppConfigService } from '../config/app-config.service';

@Injectable({ providedIn: 'root' })
export class MemoryService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/memories`;
  }

  list(): Observable<ProfileMemory[]> {
    return this.http.get<ProfileMemory[]>(this.baseUrl);
  }

  create(content: string, category: ProfileMemory['category'] = 'fact'): Observable<ProfileMemory> {
    return this.http.post<ProfileMemory>(this.baseUrl, { content, category });
  }

  update(id: string, changes: Partial<Pick<ProfileMemory, 'content' | 'category' | 'enabled'>>): Observable<ProfileMemory> {
    return this.http.patch<ProfileMemory>(`${this.baseUrl}/${id}`, changes);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }

  forgetAll(): Observable<void> {
    return this.http.delete<void>(this.baseUrl);
  }

  getSettings(): Observable<{ memory_enabled: boolean }> {
    return this.http.get<{ memory_enabled: boolean }>(`${this.baseUrl}/settings`);
  }

  setSettings(enabled: boolean): Observable<{ memory_enabled: boolean }> {
    return this.http.put<{ memory_enabled: boolean }>(`${this.baseUrl}/settings`, {
      memory_enabled: enabled,
    });
  }
}
