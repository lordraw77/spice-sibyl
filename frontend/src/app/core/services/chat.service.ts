/**
 * ChatService — thin HTTP client for the SpiceSibyl chat API.
 *
 * Wraps POST /chat/completions (both regular and SSE streaming) and GET /models.
 * The base URL is resolved at runtime from AppConfigService so the same build
 * works in every environment.
 */
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { ChatCompletionRequest, ChatCompletionResponse, ChatModel, ChatStreamEvent, ProviderStatus, ProviderSummary, ProviderTestResult } from '../models/chat.models';
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

  /**
   * Open an SSE stream for a chat completion request.
   *
   * Uses the Fetch API with a ReadableStream reader because Angular's HttpClient
   * does not support server-sent events over POST.  Each emitted value contains
   * the SSE event name and the parsed JSON data object.  The observable completes
   * when the backend sends the [DONE] sentinel or the stream closes.
   */
  stream(payload: ChatCompletionRequest): Observable<ChatStreamEvent> {
    const url = `${this.apiUrl}/chat/completions`;
    const body = JSON.stringify({ ...payload, stream: true });

    return new Observable(subscriber => {
      const controller = new AbortController();

      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body,
        signal: controller.signal,
      })
        .then(response => {
          if (!response.ok) {
            subscriber.error(new Error(`HTTP ${response.status}`));
            return;
          }

          const reader = response.body!.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          let currentEvent = 'message';

          const pump = (): void => {
            reader.read().then(({ done, value }) => {
              if (done) {
                subscriber.complete();
                return;
              }

              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split('\n');
              buffer = lines.pop() ?? '';

              for (const line of lines) {
                if (line.startsWith('event:')) {
                  currentEvent = line.slice(6).trim();
                } else if (line.startsWith('data:')) {
                  const raw = line.slice(5).trim();
                  if (raw === '[DONE]') {
                    subscriber.complete();
                    return;
                  }
                  try {
                    subscriber.next({ event: currentEvent, data: JSON.parse(raw) });
                  } catch { /* skip malformed chunk */ }
                  currentEvent = 'message';
                }
              }

              pump();
            }).catch(err => {
              if ((err as Error).name !== 'AbortError') {
                subscriber.error(err);
              }
            });
          };

          pump();
        })
        .catch(err => subscriber.error(err));

      return () => controller.abort();
    });
  }

  /** Fetch the full model list along with a per-provider summary. */
  models(): Observable<{ object: string; data: ChatModel[]; providers: ProviderSummary[] }> {
    return this.http.get<{ object: string; data: ChatModel[]; providers: ProviderSummary[] }>(`${this.apiUrl}/models`);
  }

  /** Return all providers with live configuration status. */
  providerStatuses(): Observable<ProviderStatus[]> {
    return this.http.get<ProviderStatus[]>(`${this.apiUrl}/providers`);
  }

  /** Test connectivity to a single provider. */
  testProvider(providerId: string): Observable<ProviderTestResult> {
    return this.http.post<ProviderTestResult>(`${this.apiUrl}/providers/${providerId}/test`, {});
  }

  /** Enable or disable a provider. */
  updateProvider(providerId: string, enabled: boolean): Observable<ProviderStatus> {
    return this.http.patch<ProviderStatus>(`${this.apiUrl}/providers/${providerId}`, { enabled });
  }

  /** Store an API key override for a provider. */
  setProviderKey(providerId: string, apiKey: string): Observable<{ ok: boolean; configured: boolean }> {
    return this.http.put<{ ok: boolean; configured: boolean }>(
      `${this.apiUrl}/providers/${providerId}/key`,
      { api_key: apiKey },
    );
  }
}
