import { Injectable, inject, signal } from '@angular/core';

import { AppConfigService } from '../../core/config/app-config.service';
import { AuthService } from '../../core/services/auth.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { NotificationService } from '../../core/services/notification.service';
import { ProfileService } from '../../core/services/profile.service';
import { TelegramLinkStatus } from '../../core/models/chat.models';

/**
 * Pairing the active profile with a Telegram account (roadmap v2 § 3, P2 —
 * extracted from ChatPageComponent).
 *
 * Three calls and their own three pieces of state, unrelated to anything else
 * the chat page does. The raw fetch() calls are kept as they were: they bypass
 * the auth interceptor, so the bearer token is attached here.
 */
@Injectable()
export class TelegramLinkService {
  private readonly appConfig = inject(AppConfigService);
  private readonly auth = inject(AuthService);
  private readonly i18n = inject(I18nService);
  private readonly notifications = inject(NotificationService);
  private readonly profileService = inject(ProfileService);

  readonly status = signal<TelegramLinkStatus>({ linked: false });
  /** Bound to the input where the user pastes the code the bot gave them. */
  code = '';
  loading = false;

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const headers: Record<string, string> = { ...extra };
    const token = this.auth.token;
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return headers;
  }

  /** Refresh the badge. Failures are silent: an unknown state shows as unlinked. */
  load(): void {
    const profileId = this.profileService.currentId;
    fetch(`${this.appConfig.apiUrl}/telegram/link/${profileId}`, { headers: this.headers() })
      .then((r) => r.json())
      .then((data) => this.status.set(data))
      .catch(() => {});
  }

  submit(): void {
    const code = this.code.trim();
    if (!code) return;
    this.loading = true;
    fetch(`${this.appConfig.apiUrl}/telegram/link`, {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ code, profile_id: this.profileService.currentId }),
    })
      .then((r) => { if (!r.ok) throw new Error(); return r.json(); })
      .then((data) => {
        this.status.set(data);
        this.code = '';
        this.loading = false;
        this.notifications.add(
          'success',
          this.i18n.translate('chat.telegram.linkedTitle'),
          this.i18n.translate('chat.telegram.linkedBody', {
            user: data.username || this.i18n.translate('chat.telegram.unknown'),
          }),
        );
      })
      .catch(() => {
        this.loading = false;
        this.notifications.add(
          'error',
          this.i18n.translate('common.error'),
          this.i18n.translate('chat.telegram.invalidCode'),
        );
      });
  }

  unlink(): void {
    fetch(`${this.appConfig.apiUrl}/telegram/link/${this.profileService.currentId}`, {
      method: 'DELETE',
      headers: this.headers(),
    })
      .then(() => {
        this.status.set({ linked: false });
        this.notifications.add(
          'success',
          this.i18n.translate('chat.telegram.unlinkedTitle'),
          this.i18n.translate('chat.telegram.unlinkedBody'),
        );
      })
      .catch(() =>
        this.notifications.add(
          'error',
          this.i18n.translate('common.error'),
          this.i18n.translate('chat.telegram.unlinkFailed'),
        ),
      );
  }
}
