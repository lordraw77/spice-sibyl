import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, firstValueFrom, tap } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';

export interface AuthUser {
  id: string;
  email: string;
  role: 'admin' | 'user' | 'read-only';
  disabled?: boolean;
  created_at?: number;
}

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

const REFRESH_KEY = 'spicesibyl_refresh';
const PROFILE_KEY = 'spicesibyl_profile';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  /** In-memory access token (never persisted — refreshed from the stored refresh token). */
  private accessToken: string | null = null;

  readonly currentUser = signal<AuthUser | null>(null);
  readonly isAuthenticated = computed(() => this.currentUser() !== null);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/auth`;
  }

  get token(): string | null {
    return this.accessToken;
  }

  hasRole(...roles: AuthUser['role'][]): boolean {
    const user = this.currentUser();
    return !!user && roles.includes(user.role);
  }

  /**
   * Restore a session on app start: if a refresh token is stored, exchange it
   * for a fresh access token and load the user.  Resolves regardless of outcome
   * so bootstrap never blocks.
   */
  async bootstrap(): Promise<void> {
    const refresh = localStorage.getItem(REFRESH_KEY);
    if (!refresh) return;
    try {
      await this.exchangeRefresh(refresh);
      await firstValueFrom(this.loadMe());
    } catch {
      this.clearSession();
    }
  }

  login(email: string, password: string): Observable<AuthUser> {
    return new Observable<AuthUser>((sub) => {
      this.http.post<TokenResponse>(`${this.baseUrl}/login`, { email, password }).subscribe({
        next: async (tokens) => {
          // A fresh login may be a different user — never inherit the old profile.
          localStorage.removeItem(PROFILE_KEY);
          this.storeTokens(tokens);
          try {
            const user = await firstValueFrom(this.loadMe());
            sub.next(user);
            sub.complete();
          } catch (err) {
            sub.error(err);
          }
        },
        error: (err) => sub.error(err),
      });
    });
  }

  /** Exchange a refresh token for a new token pair (used at boot and on 401). */
  async refresh(): Promise<boolean> {
    const stored = localStorage.getItem(REFRESH_KEY);
    if (!stored) return false;
    try {
      await this.exchangeRefresh(stored);
      return true;
    } catch {
      this.clearSession();
      return false;
    }
  }

  logout(): Observable<void> {
    const refresh = localStorage.getItem(REFRESH_KEY);
    const done = () => this.clearSession();
    if (!refresh) {
      done();
      return new Observable<void>((s) => { s.next(); s.complete(); });
    }
    return this.http
      .post<void>(`${this.baseUrl}/logout`, { refresh_token: refresh })
      .pipe(tap({ next: done, error: done }));
  }

  loadMe(): Observable<AuthUser> {
    return this.http
      .get<AuthUser>(`${this.baseUrl}/me`)
      .pipe(tap((user) => this.currentUser.set(user)));
  }

  private async exchangeRefresh(refreshToken: string): Promise<void> {
    const tokens = await firstValueFrom(
      this.http.post<TokenResponse>(`${this.baseUrl}/refresh`, { refresh_token: refreshToken })
    );
    this.storeTokens(tokens);
  }

  private storeTokens(tokens: TokenResponse): void {
    this.accessToken = tokens.access_token;
    localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  }

  private clearSession(): void {
    this.accessToken = null;
    this.currentUser.set(null);
    localStorage.removeItem(REFRESH_KEY);
    // Drop the previously selected profile so the next user is never scoped to
    // someone else's profile (the backend would otherwise 403 on it).
    localStorage.removeItem(PROFILE_KEY);
  }
}
