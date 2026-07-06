import { Injectable, signal } from '@angular/core';

const STORAGE_KEY = 'spicesibyl_reminder_tz';

/**
 * Per-user default IANA timezone for reminders created from the web UI
 * (Phase 23.d). Roams via SettingsSyncService's user blob (reminderTimezone
 * key) like theme/accent/notifyPrefs. Null = no override, use the reminder's
 * own field or the backend default.
 */
@Injectable({ providedIn: 'root' })
export class ReminderPrefsService {
  readonly timezone = signal<string | null>(this.load());

  private load(): string | null {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  }

  private persistLocal(value: string | null): void {
    try {
      if (value) localStorage.setItem(STORAGE_KEY, value);
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore quota/availability errors */
    }
  }

  set(value: string | null): void {
    this.timezone.set(value);
    this.persistLocal(value);
  }

  /** Apply the value restored from the backend. Never prompts anything. */
  hydrate(value: string | null | undefined): void {
    if (value === undefined) return;
    this.timezone.set(value);
    this.persistLocal(value);
  }
}
