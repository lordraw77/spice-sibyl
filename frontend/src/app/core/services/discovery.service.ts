import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AppConfigService } from '../config/app-config.service';

export interface DiscoveryModel {
  id: string;
  name: string;
  label: string;
  free: boolean;
  capabilities: string[];
}

export interface DiscoveryResult {
  model_count: number;
  yaml: string;
  models: DiscoveryModel[];
}

@Injectable({ providedIn: 'root' })
export class DiscoveryService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  runCloudflareDiscovery(): Observable<DiscoveryResult> {
    return this.http.post<DiscoveryResult>(
      `${this.config.apiUrl}/cloudflare-discovery/run`,
      {}
    );
  }

  runOpenRouterDiscovery(): Observable<DiscoveryResult> {
    return this.http.post<DiscoveryResult>(
      `${this.config.apiUrl}/openrouter-discovery/run`,
      {}
    );
  }
}