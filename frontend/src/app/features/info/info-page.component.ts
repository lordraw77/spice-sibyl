/**
 * InfoPageComponent — service information page.
 *
 * Shows the web UI version (from package.json at build time), backend metadata
 * from GET /v1/info (version, environment, runtime, uptime, feature flags),
 * the API endpoint in use and live health/readiness status.
 */
import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';

import { AppConfigService } from '../../core/config/app-config.service';
import packageJson from '../../../../package.json';

interface BackendInfo {
  name: string;
  version: string;
  environment: string;
  api_base: string;
  python_version: string;
  platform: string;
  started_at: number;
  uptime_seconds: number;
  default_model: string;
  timezone: string;
  providers_configured: number;
  db: { path: string; size_bytes: number | null };
  response_cache: { entries: number; hits: number; misses: number };
  features: Record<string, boolean>;
}

interface ReadyStatus {
  status: string;
  checks: { db: boolean; providers: number };
}

const FEATURE_LABELS: Record<string, string> = {
  memory: 'Memoria persistente',
  auto_title: 'Auto-titling LLM',
  response_cache: 'Response cache',
  code_interpreter: 'Code interpreter (python_exec)',
  telegram_bot: 'Bot Telegram',
  backup: 'Backup schedulati',
  rag_hybrid: 'RAG ibrido (FTS5 + vettori)',
  discovery_refresh: 'Discovery automatica modelli',
  chat_fallback_chain: 'Fallback chain chat',
  orchestrator: 'Orchestratore Multi-MCP',
};

@Component({
  selector: 'app-info-page',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './info-page.component.html',
  styleUrl: './info-page.component.css',
})
export class InfoPageComponent implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  readonly uiVersion = packageJson.version;
  readonly info = signal<BackendInfo | null>(null);
  readonly infoError = signal(false);
  readonly healthOk = signal<boolean | null>(null);
  readonly ready = signal<ReadyStatus | null>(null);

  get apiUrl(): string {
    return this.config.apiUrl;
  }

  /** Absolute endpoint URL shown to the user (resolves relative apiUrl). */
  get endpointUrl(): string {
    try {
      return new URL(this.apiUrl, window.location.origin).toString();
    } catch {
      return this.apiUrl;
    }
  }

  get docsUrl(): string {
    // FastAPI docs live at the backend root, outside /api/v1.
    return new URL('/docs', window.location.origin).toString();
  }

  featureEntries(features: Record<string, boolean>): { label: string; enabled: boolean }[] {
    return Object.entries(features).map(([key, enabled]) => ({
      label: FEATURE_LABELS[key] ?? key,
      enabled,
    }));
  }

  formatUptime(seconds: number): string {
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}g ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  formatBytes(bytes: number | null): string {
    if (bytes == null) return '—';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  }

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.http.get<BackendInfo>(`${this.apiUrl}/info`).subscribe({
      next: data => { this.info.set(data); this.infoError.set(false); },
      error: () => this.infoError.set(true),
    });
    this.http.get<{ status: string }>(`${this.apiUrl}/health`).subscribe({
      next: h => this.healthOk.set(h.status === 'ok'),
      error: () => this.healthOk.set(false),
    });
    this.http.get<ReadyStatus>(`${this.apiUrl}/ready`).subscribe({
      next: r => this.ready.set(r),
      error: () => this.ready.set({ status: 'not_ready', checks: { db: false, providers: 0 } }),
    });
  }
}
