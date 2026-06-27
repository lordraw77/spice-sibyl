import { Injectable, signal } from '@angular/core';

const STORAGE_KEY = 'spicesibyl_notify_enabled';

/**
 * Local (client-side) notifications for long-running generations.
 *
 * No web-push / VAPID / backend involved: when a streaming completion finishes
 * while the tab is hidden, we surface a system Notification so the user — who
 * may have switched away — knows the reply is ready. The notification is only
 * shown when the user has both opted in and granted browser permission.
 */
@Injectable({ providedIn: 'root' })
export class PushNotifyService {
  /** User opt-in toggle, persisted in localStorage. Default OFF. */
  readonly enabled = signal<boolean>(this.loadEnabled());

  private loadEnabled(): boolean {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  }

  /** True when the Notification API is available in this browser. */
  get supported(): boolean {
    return typeof Notification !== 'undefined';
  }

  get permission(): NotificationPermission {
    return this.supported ? Notification.permission : 'denied';
  }

  /**
   * Toggle the opt-in. Enabling triggers a permission request; if the user
   * denies it, the toggle is reverted to OFF.
   */
  async toggle(): Promise<void> {
    if (this.enabled()) {
      this.setEnabled(false);
      return;
    }
    const granted = await this.requestPermission();
    this.setEnabled(granted);
  }

  private setEnabled(value: boolean): void {
    this.enabled.set(value);
    try {
      localStorage.setItem(STORAGE_KEY, String(value));
    } catch {
      /* ignore quota/availability errors */
    }
  }

  /** Ask the browser for notification permission. Resolves to true if granted. */
  async requestPermission(): Promise<boolean> {
    if (!this.supported) return false;
    if (Notification.permission === 'granted') return true;
    if (Notification.permission === 'denied') return false;
    try {
      const result = await Notification.requestPermission();
      return result === 'granted';
    } catch {
      return false;
    }
  }

  /**
   * Show a completion notification when (and only when) the user opted in, the
   * tab is hidden, and permission was granted. Clicking it refocuses the app
   * and runs the optional onClick callback (e.g. navigate to the conversation).
   */
  async notifyComplete(title: string, body: string, onClick?: () => void): Promise<void> {
    if (!this.enabled() || !this.supported) return;
    if (!document.hidden) return;
    if (Notification.permission !== 'granted') return;

    const options: NotificationOptions = {
      body,
      icon: 'icons/icon-192.png',
      badge: 'icons/icon-192.png',
      tag: 'spicesibyl-generation',
      renotify: true,
    } as NotificationOptions;

    const handleClick = () => {
      window.focus();
      onClick?.();
    };

    // Prefer the service worker registration (more reliable when backgrounded),
    // falling back to a page-level Notification when no SW is active (e.g. dev).
    try {
      if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
        const reg = await navigator.serviceWorker.ready;
        await reg.showNotification(title, options);
        return;
      }
    } catch {
      /* fall through to page-level Notification */
    }

    try {
      const notification = new Notification(title, options);
      notification.onclick = (event) => {
        event.preventDefault();
        handleClick();
        notification.close();
      };
    } catch {
      /* notification construction not allowed; silently ignore */
    }
  }
}
