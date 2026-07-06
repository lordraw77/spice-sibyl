import { Injectable, signal } from '@angular/core';

/**
 * Cross-channel notification event types (Phase 23.c). Must match the
 * event_type strings used by the backend (app/services/notification_service.py).
 */
export type NotifyEventType =
  | 'workflowDone'
  | 'workflowFailed'
  | 'imageGenDone'
  | 'longCompletionDone'
  | 'reminderFired'
  | 'kbIngested';

export type NotifyPrefs = Record<NotifyEventType, boolean>;

const STORAGE_KEY = 'spicesibyl_notify_prefs';

const DEFAULT_PREFS: NotifyPrefs = {
  workflowDone: true,
  workflowFailed: true,
  imageGenDone: true,
  longCompletionDone: true,
  reminderFired: true,
  kbIngested: true,
};

/**
 * Per-event-type opt-in matrix for the Phase 23.c notification bridge. Local
 * default is ON for every event; roams via SettingsSyncService's user blob
 * (notifyPrefs key) like theme/accent/pushEnabled.
 */
@Injectable({ providedIn: 'root' })
export class NotificationPrefsService {
  readonly prefs = signal<NotifyPrefs>(this.load());

  private load(): NotifyPrefs {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? { ...DEFAULT_PREFS, ...JSON.parse(raw) } : { ...DEFAULT_PREFS };
    } catch {
      return { ...DEFAULT_PREFS };
    }
  }

  private persistLocal(value: NotifyPrefs): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    } catch {
      /* ignore quota/availability errors */
    }
  }

  set(type: NotifyEventType, enabled: boolean): void {
    const next = { ...this.prefs(), [type]: enabled };
    this.prefs.set(next);
    this.persistLocal(next);
  }

  /** Apply the matrix restored from the backend. Never prompts anything. */
  hydrate(value: Partial<NotifyPrefs>): void {
    const next = { ...this.prefs(), ...value };
    this.prefs.set(next);
    this.persistLocal(next);
  }
}
