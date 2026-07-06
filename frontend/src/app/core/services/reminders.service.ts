import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';

/** Phase 23.d — extended reminders: one-shot/recurring/cron, per-channel delivery. */

export interface ReminderOut {
  id: string;
  text: string | null;
  smart_prompt: string | null;
  recurrence: string;
  fire_at: number;
  timezone: string | null;
  channels: string;
  active: boolean;
  fired: boolean;
  created_at: number;
  last_fired_at: number | null;
}

export interface ReminderCreate {
  text?: string;
  smart_prompt?: string;
  recurrence: string;
  fire_at: number;
  timezone?: string;
  channels: string;
}

export interface ReminderPatch {
  text?: string;
  smart_prompt?: string;
  recurrence?: string;
  fire_at?: number;
  timezone?: string;
  channels?: string;
  active?: boolean;
}

@Injectable({ providedIn: 'root' })
export class RemindersService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  readonly reminders = signal<ReminderOut[]>([]);

  private get base(): string {
    return `${this.config.apiUrl}/reminders`;
  }

  list(): Observable<ReminderOut[]> {
    return this.http.get<ReminderOut[]>(this.base).pipe(tap((list) => this.reminders.set(list)));
  }

  create(payload: ReminderCreate): Observable<ReminderOut> {
    return this.http.post<ReminderOut>(this.base, payload).pipe(tap(() => this.refresh()));
  }

  patch(id: string, payload: ReminderPatch): Observable<ReminderOut> {
    return this.http.patch<ReminderOut>(`${this.base}/${id}`, payload).pipe(tap(() => this.refresh()));
  }

  remove(id: string): Observable<{ ok: true }> {
    return this.http.delete<{ ok: true }>(`${this.base}/${id}`).pipe(tap(() => this.refresh()));
  }

  snooze(id: string, minutes = 10): Observable<ReminderOut> {
    return this.http
      .post<ReminderOut>(`${this.base}/${id}/snooze`, { minutes })
      .pipe(tap(() => this.refresh()));
  }

  repeat(id: string): Observable<ReminderOut> {
    return this.http.post<ReminderOut>(`${this.base}/${id}/repeat`, {}).pipe(tap(() => this.refresh()));
  }

  private refresh(): void {
    this.http.get<ReminderOut[]>(this.base).subscribe({
      next: (list) => this.reminders.set(list),
      error: () => {},
    });
  }
}
