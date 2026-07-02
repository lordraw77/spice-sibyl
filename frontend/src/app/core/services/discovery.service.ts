/**
 * DiscoveryService — HTTP client for the unified provider discovery endpoint.
 *
 * POST /v1/providers/{id}/discover queries the provider's live model catalog
 * on the backend and persists the result in the discovered-models catalog;
 * the returned list is shown as a preview.
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

/** Full payload returned by POST /providers/{id}/discover */
export interface DiscoveryResult {
  provider_id: string;
  model_count: number;
  models: DiscoveryModel[];
  /** Unix timestamp of when the catalog was saved on the backend */
  discovered_at: number | null;
  saved: boolean;
}

@Injectable({ providedIn: 'root' })
export class DiscoveryService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  /** Fetch and persist the live model catalog for the given provider. */
  runDiscovery(providerId: string): Observable<DiscoveryResult> {
    return this.http.post<DiscoveryResult>(
      `${this.config.apiUrl}/providers/${providerId}/discover`,
      {}
    );
  }
}
