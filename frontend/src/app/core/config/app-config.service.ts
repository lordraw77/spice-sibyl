/**
 * AppConfigService — loads runtime configuration before the app bootstraps.
 *
 * The config file (/config/app-config.json) is injected by the Docker entrypoint
 * from an environment-specific template, allowing the same container image to be
 * used across environments without rebuilding.
 *
 * Usage: call load() in the APP_INITIALIZER factory so the apiUrl is available
 * before any HTTP request is made.
 */
import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

import { AppConfig } from './app-config.model';

@Injectable({ providedIn: 'root' })
export class AppConfigService {
  // Sensible default for local development without Docker
  private config: AppConfig = {
    apiUrl: '/api/v1'
  };

  constructor(private http: HttpClient) {}

  /** Fetch and store the runtime config.  Call once during app initialization. */
  async load(): Promise<void> {
    const loaded = await firstValueFrom(
      this.http.get<AppConfig>('/config/app-config.json')
    );
    this.config = loaded;
  }

  /** The resolved backend API base URL (e.g. http://api.example.com/api/v1). */
  get apiUrl(): string {
    return this.config.apiUrl;
  }
}
