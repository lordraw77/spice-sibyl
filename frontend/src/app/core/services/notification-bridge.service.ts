/**
 * NotificationBridgeService — Phase 23.c cross-channel notification bridge.
 *
 * Web → Telegram: trigger() posts a client-observed event (one the backend
 * can't see, e.g. "long completion finished while the tab was hidden") so it
 * still reaches Telegram for linked users.
 *
 * Telegram → Web: connect() opens a fetch-based SSE stream (same technique as
 * ChatService.stream — native EventSource can't send the Bearer header this
 * API requires) and turns each event into a toast + unread-count bump.
 */
import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';
import { AuthService } from './auth.service';
import { NotificationService } from './notification.service';
import { NotifyEventType } from './notification-prefs.service';

interface NotificationEvent {
  id: string;
  event_type: string;
  title: string;
  body: string;
  created_at: number;
}

interface NotificationListResponse {
  items: NotificationEvent[];
  unread_count: number;
}

@Injectable({ providedIn: 'root' })
export class NotificationBridgeService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);
  private readonly auth = inject(AuthService);
  private readonly toasts = inject(NotificationService);

  readonly unreadCount = signal(0);

  private controller: AbortController | null = null;

  private get apiUrl(): string {
    return `${this.config.apiUrl}/notifications`;
  }

  /** Forward a client-only observation (backend has no way to see it) to Telegram. */
  async trigger(eventType: NotifyEventType, title: string, body = ''): Promise<void> {
    try {
      await firstValueFrom(
        this.http.post(`${this.apiUrl}/trigger`, { event_type: eventType, title, body }),
      );
    } catch {
      /* best-effort — no linked profile, opted out, or offline */
    }
  }

  /** Fetch the current unread count / recent list once (e.g. on app boot). */
  async refresh(): Promise<void> {
    try {
      const res = await firstValueFrom(
        this.http.get<NotificationListResponse>(this.apiUrl),
      );
      this.unreadCount.set(res?.unread_count ?? 0);
    } catch {
      /* offline / first run */
    }
  }

  markRead(id: string): void {
    firstValueFrom(this.http.post(`${this.apiUrl}/${id}/read`, {}))
      .then(() => this.unreadCount.update(n => Math.max(0, n - 1)))
      .catch(() => void 0);
  }

  /** Open the live SSE stream; call once after login. disconnect() on logout. */
  connect(): void {
    if (this.controller || !this.auth.isAuthenticated()) return;
    this.controller = new AbortController();
    void this.refresh();
    void this.pump(this.controller);
  }

  disconnect(): void {
    this.controller?.abort();
    this.controller = null;
  }

  private async pump(controller: AbortController): Promise<void> {
    const headers: Record<string, string> = { Accept: 'text/event-stream' };
    const token = this.auth.token;
    if (token) headers['Authorization'] = `Bearer ${token}`;

    try {
      const response = await fetch(`${this.apiUrl}/stream`, { headers, signal: controller.signal });
      if (!response.ok || !response.body) return;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = 'message';

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (line.startsWith('event:')) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith('data:')) {
            const raw = line.slice(5).trim();
            if (currentEvent === 'notification' && raw) {
              this.handleEvent(JSON.parse(raw) as NotificationEvent);
            }
          }
        }
      }
    } catch {
      /* aborted on disconnect, or transient network error — no auto-retry for now */
    }
  }

  private handleEvent(event: NotificationEvent): void {
    this.unreadCount.update(n => n + 1);
    this.toasts.add('info', event.title, event.body, 8000, () => this.markRead(event.id));
  }
}
