import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap } from 'rxjs';

import { Profile } from '../models/chat.models';
import { AppConfigService } from '../config/app-config.service';

const STORAGE_KEY = 'spicesibyl_profile';

@Injectable({ providedIn: 'root' })
export class ProfileService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/profiles`;
  }

  /** Currently active profile, null = none selected yet */
  readonly current = signal<Profile | null>(this._loadFromStorage());

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
  }

  clear(): void {
    this.current.set(null);
    this._clearStorage();
  }
}
