import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

import { AppConfig } from './app-config.model';

@Injectable({ providedIn: 'root' })
export class AppConfigService {
  private config: AppConfig = {
    apiUrl: 'http://localhost:8000/api/v1'
  };

  constructor(private http: HttpClient) {}

  async load(): Promise<void> {
    const loaded = await firstValueFrom(
      this.http.get<AppConfig>('/config/app-config.json')
    );
    this.config = loaded;
  }

  get apiUrl(): string {
    return this.config.apiUrl;
  }
}
