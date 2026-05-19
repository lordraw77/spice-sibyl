/**
 * ChatService — thin HTTP client for the SpiceSibyl chat API.
 *
 * Wraps POST /chat/completions and GET /models.  The base URL is resolved at
 * runtime from AppConfigService so the same build works in every environment.
 */
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { ChatCompletionRequest, ChatCompletionResponse, ChatModel, ProviderSummary } from '../models/chat.models';
import { AppConfigService } from '../config/app-config.service';

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get apiUrl(): string {
    return this.config.apiUrl;
  }

  /** Send a chat completion request and return a single response observable. */
  complete(payload: ChatCompletionRequest): Observable<ChatCompletionResponse> {
    return this.http.post<ChatCompletionResponse>(`${this.apiUrl}/chat/completions`, payload);
  }

  /** Fetch the full model list along with a per-provider summary. */
  models(): Observable<{ object: string; data: ChatModel[]; providers: ProviderSummary[] }> {
    return this.http.get<{ object: string; data: ChatModel[]; providers: ProviderSummary[] }>(`${this.apiUrl}/models`);
  }
}
