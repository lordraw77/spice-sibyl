/**
 * DiscoveryService — HTTP client for the provider model-discovery endpoints.
 *
 * Each discovery call hits a backend endpoint that queries the respective
 * provider's API and returns a structured model list plus a YAML config block
 * ready to paste into provider_models.yaml.
 */
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AppConfigService } from '../config/app-config.service';

/** A single model entry returned by a discovery run */
export interface DiscoveryModel {
  id: string;
  name: string;
  label: string;
  free: boolean;
  capabilities: string[];
}

/** Full payload returned by a discovery endpoint */
export interface DiscoveryResult {
  model_count: number;
  /** YAML snippet ready to paste into provider_models.yaml */
  yaml: string;
  models: DiscoveryModel[];
}

@Injectable({ providedIn: 'root' })
export class DiscoveryService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  /** Trigger a Cloudflare Workers AI model catalog fetch on the backend. */
  runCloudflareDiscovery(): Observable<DiscoveryResult> {
    return this.http.post<DiscoveryResult>(
      `${this.config.apiUrl}/cloudflare-discovery/run`,
      {}
    );
  }

  /** Trigger an OpenRouter model catalog fetch on the backend. */
  runOpenRouterDiscovery(): Observable<DiscoveryResult> {
    return this.http.post<DiscoveryResult>(
      `${this.config.apiUrl}/openrouter-discovery/run`,
      {}
    );
  }
}