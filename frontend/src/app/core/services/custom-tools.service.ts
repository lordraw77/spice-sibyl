import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';

/** Phase 18 — user-defined custom tools (per profile). HTTP-backed functions
 *  registered from the UI and injected into the chat tool loop as
 *  `custom__<name>`. Profile scoping rides on the X-Profile-ID interceptor. */

export interface CustomToolAuth {
  type: 'none' | 'bearer' | 'header';
  token?: string | null;
  name?: string | null;
  value?: string | null;
}

export interface CustomToolEndpoint {
  url: string;
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  headers?: Record<string, string>;
  auth?: CustomToolAuth;
  timeout?: number;
}

export interface CustomTool {
  id: string;
  profile_id: string;
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  endpoint: CustomToolEndpoint;
  enabled: boolean;
  created_at: number;
  updated_at: number;
}

export interface CustomToolIn {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  endpoint: CustomToolEndpoint;
  enabled: boolean;
}

export interface CustomToolTestResult {
  ok: boolean;
  result: string;
}

@Injectable({ providedIn: 'root' })
export class CustomToolsService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get base(): string {
    return `${this.config.apiUrl}/tools/custom`;
  }

  list(): Observable<CustomTool[]> {
    return this.http.get<CustomTool[]>(this.base);
  }

  create(body: CustomToolIn): Observable<CustomTool> {
    return this.http.post<CustomTool>(this.base, body);
  }

  setEnabled(id: string, enabled: boolean): Observable<CustomTool> {
    return this.http.patch<CustomTool>(`${this.base}/${id}`, { enabled });
  }

  remove(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }

  test(id: string, args: Record<string, unknown>): Observable<CustomToolTestResult> {
    return this.http.post<CustomToolTestResult>(`${this.base}/${id}/test`, { arguments: args });
  }
}
