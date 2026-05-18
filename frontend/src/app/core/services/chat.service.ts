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

  complete(payload: ChatCompletionRequest): Observable<ChatCompletionResponse> {
    return this.http.post<ChatCompletionResponse>(`${this.apiUrl}/chat/completions`, payload);
  }

  models(): Observable<{ object: string; data: ChatModel[]; providers: ProviderSummary[] }> {
    return this.http.get<{ object: string; data: ChatModel[]; providers: ProviderSummary[] }>(`${this.apiUrl}/models`);
  }
}
