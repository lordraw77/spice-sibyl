import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap } from 'rxjs';

import { Profile } from '../models/chat.models';
import { AppConfigService } from '../config/app-config.service';
import { I18nService } from '../i18n/i18n.service';

const STORAGE_KEY = 'spicesibyl_profile';

@Injectable({ providedIn: 'root' })
export class ProfileService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);
  private readonly i18n = inject(I18nService);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/profiles`;
  }

  /** Currently active profile, null = none selected yet */
  readonly current = signal<Profile | null>(this._loadFromStorage());

  constructor() {
    // Adopt the restored profile's persisted UI language on startup (unless the
    // user already has an explicit local choice). Phase 22.
    this.i18n.adoptProfileLocale(this.current()?.locale ?? null);
  }

  private _loadFromStorage(): Profile | null {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  private _saveToStorage(profile: Profile): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
  }

  private _clearStorage(): void {
    localStorage.removeItem(STORAGE_KEY);
  }

  get currentId(): string {
    return this.current()?.id ?? 'default';
  }

  list(): Observable<Profile[]> {
    return this.http.get<Profile[]>(this.baseUrl);
  }

  create(name: string): Observable<Profile> {
    return this.http.post<Profile>(this.baseUrl, { name }).pipe(
      tap(profile => this.select(profile))
    );
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`).pipe(
      tap(() => {
        if (this.current()?.id === id) {
          this.current.set(null);
          this._clearStorage();
        }
      })
    );
  }

  select(profile: Profile): void {
    this.current.set(profile);
    this._saveToStorage(profile);
    this.i18n.adoptProfileLocale(profile.locale ?? null);
  }

  /**
   * Phase 22: persist the UI locale on the active profile so the choice follows
   * the user across devices. No-op when no profile is selected (localStorage in
   * I18nService still holds the choice locally).
   */
  updateLocale(locale: string): Observable<Profile> | null {
    const profile = this.current();
    if (!profile) return null;
    return this.http
      .patch<Profile>(`${this.baseUrl}/${profile.id}`, { locale })
      .pipe(tap((updated) => this.select(updated)));
  }

  clear(): void {
    this.current.set(null);
    this._clearStorage();
  }
}
