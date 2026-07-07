import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';

/** Phase 18 — MCP server registry (admin only). Servers are stored in the
 *  standard `mcpServers` config shape and launched over stdio at runtime. */

export interface McpServerConfig {
  command: string;
  args: string[];
  env?: Record<string, string>;
  cwd?: string | null;
  [key: string]: unknown;
}

export interface McpToolInfo {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface McpServer {
  id: string;
  name: string;
  config: McpServerConfig;
  enabled: boolean;
  created_at: number;
  updated_at: number;
  status: 'ok' | 'error' | 'disabled' | 'unknown';
  error?: string | null;
  tools: McpToolInfo[];
}

export interface McpServerIn {
  name: string;
  config: McpServerConfig;
  enabled: boolean;
}

export interface McpConfigBundle {
  mcpServers: Record<string, McpServerConfig>;
}

/** Phase 23.5.b — which stdio launchers this backend image has on PATH. */
export interface McpRuntimeReport {
  stdio_enabled: boolean;
  allowed_commands: string[];
  runtimes: Record<string, boolean>;
}

/** Phase 23.5.d — what one server in a pasted bundle needs from this deployment. */
export interface McpDeploymentCheckItem {
  name: string;
  transport: 'stdio' | 'sse';
  command?: string | null;
  requirement: string;
  ok: boolean;
  detail: string;
}

@Injectable({ providedIn: 'root' })
export class McpService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get base(): string {
    return `${this.config.apiUrl}/mcp`;
  }

  list(probe = false): Observable<McpServer[]> {
    return this.http.get<McpServer[]>(`${this.base}/servers`, {
      params: probe ? { probe: 'true' } : {},
    });
  }

  create(body: McpServerIn): Observable<McpServer> {
    return this.http.post<McpServer>(`${this.base}/servers`, body);
  }

  test(id: string): Observable<McpServer> {
    return this.http.post<McpServer>(`${this.base}/servers/${id}/test`, {});
  }

  setEnabled(id: string, enabled: boolean): Observable<McpServer> {
    return this.http.patch<McpServer>(`${this.base}/servers/${id}`, { enabled });
  }

  remove(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/servers/${id}`);
  }

  reload(): Observable<McpServer[]> {
    return this.http.post<McpServer[]>(`${this.base}/reload`, {});
  }

  exportConfig(): Observable<McpConfigBundle> {
    return this.http.get<McpConfigBundle>(`${this.base}/config`);
  }

  importConfig(bundle: McpConfigBundle, enabled = true): Observable<McpServer[]> {
    return this.http.post<McpServer[]>(`${this.base}/import`, bundle, {
      params: { enabled: String(enabled) },
    });
  }

  runtimes(): Observable<McpRuntimeReport> {
    return this.http.get<McpRuntimeReport>(`${this.base}/runtimes`);
  }

  deploymentCheck(bundle: McpConfigBundle): Observable<McpDeploymentCheckItem[]> {
    return this.http.post<McpDeploymentCheckItem[]>(`${this.base}/deployment-check`, bundle);
  }
}
