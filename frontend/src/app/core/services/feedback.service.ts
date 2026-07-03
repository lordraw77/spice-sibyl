/**
 * FeedbackService — HTTP client for Phase 19 message feedback (👍/👎).
 *
 * Wraps the /v1/feedback endpoints: rate / clear feedback on assistant
 * messages and download the exportable evaluation dataset.
 */
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';

@Injectable({ providedIn: 'root' })
export class FeedbackService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/feedback`;
  }

  rate(messageId: string, rating: 1 | -1, note?: string): Observable<{ id: string; rating: number | null }> {
    return this.http.put<{ id: string; rating: number | null }>(
      `${this.baseUrl}/messages/${messageId}`,
      { rating, note: note || undefined },
    );
  }

  clear(messageId: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/messages/${messageId}`);
  }

  exportDataset(): Observable<Blob> {
    return this.http.get(`${this.baseUrl}/export`, { responseType: 'blob' });
  }
}
