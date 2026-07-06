import { Injectable, effect, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';
import { AuthService } from './auth.service';
import { ThemeService, ThemeMode } from './theme.service';
import { I18nService } from '../i18n/i18n.service';
import { isLocale } from '../i18n/locale';
import { OnboardingService } from './onboarding.service';
import { PushNotifyService } from './push-notify.service';
import { NotificationPrefsService, NotifyPrefs } from './notification-prefs.service';
import { ProfileService } from './profile.service';
import { UserPreferencesService, UserPreferences } from './user-preferences.service';

interface SettingsBlob {
  data: Record<string, unknown>;
}

/** UI/appearance settings that roam with the user account (not per profile). */
interface UserBlob {
  theme?: ThemeMode;
  accent?: string;
  locale?: string;
  onboarded?: boolean;
  pushEnabled?: boolean;
  notifyPrefs?: Partial<NotifyPrefs>;
}

const SAVE_DEBOUNCE_MS = 600;

/**
 * Roaming profile (Phase 23).
 *
 * Persists two scopes of settings to the backend so they follow the user across
 * devices:
 *   • user scope    — theme, accent, locale, onboarding, push opt-in
 *   • profile scope — the active profile's chat preferences (UserPreferences)
 *
 * Design: leaf services expose their state as signals; this service observes
 * them with effect() and pushes changes (debounced). It never gets injected back
 * into those services, so there is no DI cycle. Inbound restores are de-duped by
 * comparing against the last-known-server JSON, so applying a fetched blob never
 * echoes straight back as a save.
 */
@Injectable({ providedIn: 'root' })
export class SettingsSyncService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);
  private readonly auth = inject(AuthService);
  private readonly theme = inject(ThemeService);
  private readonly i18n = inject(I18nService);
  private readonly onboarding = inject(OnboardingService);
  private readonly push = inject(PushNotifyService);
  private readonly notifyPrefs = inject(NotificationPrefsService);
  private readonly profile = inject(ProfileService);
  private readonly userPrefs = inject(UserPreferencesService);

  /** Enabled only after the initial hydrate, so boot never triggers a save. */
  private ready = false;
  /** The profile whose chat prefs the save-effect currently targets. */
  private activeProfileId: string | null = null;

  private lastUserJson = '';
  private readonly lastProfileJson = new Map<string, string>();

  private userSaveTimer: ReturnType<typeof setTimeout> | null = null;
  private profileSaveTimer: ReturnType<typeof setTimeout> | null = null;

  private get baseUrl(): string {
    return `${this.config.apiUrl}/settings`;
  }

  constructor() {
    // Effect 1 — persist user-scope settings when any of them change.
    effect(() => {
      const json = JSON.stringify(this.currentUserBlob()); // tracks the signals
      if (!this.ready) return;
      if (json === this.lastUserJson) return;
      this.queueUserSave(json);
    });

    // Effect 2 — persist the active profile's chat prefs on any local edit.
    effect(() => {
      this.userPrefs.revision(); // track edits only (hydrate() does not bump it)
      if (!this.ready) return;
      const id = this.activeProfileId;
      if (!id) return; // no real profile selected → localStorage only
      const json = JSON.stringify(this.userPrefs.snapshot());
      if (json === this.lastProfileJson.get(id)) return;
      this.queueProfileSave(id, json);
    });

    // Effect 3 — when the selected profile changes, load its chat prefs.
    effect(() => {
      const id = this.profile.current()?.id ?? null;
      if (!this.ready) {
        this.activeProfileId = id;
        return;
      }
      if (id === this.activeProfileId) return;
      this.activeProfileId = id;
      if (id) void this.hydrateProfile(id);
    });
  }

  private currentUserBlob(): UserBlob {
    return {
      theme: this.theme.mode(),
      accent: this.theme.accentColor(),
      locale: this.i18n.locale(),
      onboarded: this.onboarding.seen(),
      pushEnabled: this.push.enabled(),
      notifyPrefs: this.notifyPrefs.prefs(),
    };
  }

  /**
   * Load the user-scope blob from the backend and apply it, then hydrate the
   * restored profile's chat prefs. Call once after auth is established (from the
   * APP_INITIALIZER). Enables the save-effects on completion.
   */
  async hydrateUser(): Promise<void> {
    if (!this.auth.isAuthenticated()) {
      this.ready = true;
      return;
    }
    try {
      const blob = await firstValueFrom(
        this.http.get<SettingsBlob>(`${this.baseUrl}/user`),
      );
      this.applyUser((blob?.data ?? {}) as UserBlob);
    } catch {
      /* offline / first run — keep local values */
    }
    // Record the baseline so the first save-effect run doesn't re-push identical data.
    this.lastUserJson = JSON.stringify(this.currentUserBlob());
    this.ready = true;

    const current = this.profile.current();
    this.activeProfileId = current?.id ?? null;
    if (current) await this.hydrateProfile(current.id);
  }

  private applyUser(data: UserBlob): void {
    if (data.theme === 'dark' || data.theme === 'light' || data.theme === 'system') {
      this.theme.setMode(data.theme);
    }
    if (typeof data.accent === 'string' && /^#[0-9a-fA-F]{6}$/.test(data.accent)) {
      this.theme.setAccent(data.accent);
    }
    if (isLocale(data.locale)) {
      this.i18n.setLocale(data.locale);
    }
    if (typeof data.onboarded === 'boolean') {
      this.onboarding.hydrate(data.onboarded);
    }
    if (typeof data.pushEnabled === 'boolean') {
      this.push.hydrate(data.pushEnabled);
    }
    if (data.notifyPrefs && typeof data.notifyPrefs === 'object') {
      this.notifyPrefs.hydrate(data.notifyPrefs);
    }
  }

  /** Load a profile's chat prefs from the backend and apply them locally. */
  async hydrateProfile(profileId: string): Promise<void> {
    try {
      const blob = await firstValueFrom(
        this.http.get<SettingsBlob>(`${this.baseUrl}/profile/${profileId}`),
      );
      this.userPrefs.hydrate((blob?.data ?? {}) as Partial<UserPreferences>);
    } catch {
      /* keep whatever is in localStorage */
    }
    this.lastProfileJson.set(profileId, JSON.stringify(this.userPrefs.snapshot()));
  }

  private queueUserSave(json: string): void {
    if (this.userSaveTimer) clearTimeout(this.userSaveTimer);
    this.userSaveTimer = setTimeout(() => {
      this.userSaveTimer = null;
      this.lastUserJson = json;
      firstValueFrom(
        this.http.put<SettingsBlob>(`${this.baseUrl}/user`, { data: JSON.parse(json) }),
      ).catch(() => {
        // Roll back the baseline so a later change retries the push.
        this.lastUserJson = '';
      });
    }, SAVE_DEBOUNCE_MS);
  }

  private queueProfileSave(profileId: string, json: string): void {
    if (this.profileSaveTimer) clearTimeout(this.profileSaveTimer);
    this.profileSaveTimer = setTimeout(() => {
      this.profileSaveTimer = null;
      this.lastProfileJson.set(profileId, json);
      firstValueFrom(
        this.http.put<SettingsBlob>(`${this.baseUrl}/profile/${profileId}`, {
          data: JSON.parse(json),
        }),
      ).catch(() => {
        this.lastProfileJson.delete(profileId);
      });
    }, SAVE_DEBOUNCE_MS);
  }
}
