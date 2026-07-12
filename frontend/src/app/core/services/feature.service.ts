import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';
import { AuthService } from './auth.service';

/**
 * Canonical registry of toggleable UI features (mirrors the backend
 * `FEATURE_KEYS` in app/schemas/features.py). Keys map 1:1 to navbar menu leaves
 * and the chat sidebar capability toggles. `chat`, `ops` and `settings` are
 * deliberately absent — they are always available.
 */
export const FEATURE_KEYS = [
  'providers',
  'discovery',
  'compare',
  'stats',
  'tools',
  'workflows',
  'graph_workflows',
  'reminders',
  'mcp',
  'workspaces',
  'templates',
  'tags',
  'knowledge',
  'memory',
  'help',
  'info',
] as const;

export type FeatureKey = (typeof FEATURE_KEYS)[number];

/** Catalog entry as returned by the admin model-selection endpoint. */
export interface CatalogModel {
  id: string;
  label?: string;
  provider?: string;
  free?: boolean;
  capabilities?: string[];
}

export interface CatalogProvider {
  id: string;
  label?: string;
  enabled?: boolean;
}

export interface ModelSelectionState {
  models: CatalogModel[];
  providers: CatalogProvider[];
  /** Allow-listed model ids; empty = every model visible. */
  selected: string[];
}

/** One env-derived setting, as exposed by the admin config endpoint. */
export interface ConfigEntry {
  key: string; // env-style name, e.g. DEFAULT_MODEL
  value: string;
  default: string;
  /** True when the value comes from the environment / .env, false = built-in default. */
  configured: boolean;
  description: string;
}

export interface ConfigGroup {
  id: string;
  label: string;
  entries: ConfigEntry[];
}

/**
 * Global (admin-controlled) feature toggles. Loaded once at bootstrap from
 * `GET /v1/features`; gates the menu, the sidebar toggles and the route guard.
 * A feature is enabled unless the loaded map explicitly says `false`.
 */
@Injectable({ providedIn: 'root' })
export class FeatureService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);
  private readonly auth = inject(AuthService);

  readonly flags = signal<Record<string, boolean>>({});
  /** Bumps whenever flags change, for consumers that want a plain trigger. */
  readonly revision = computed(() => Object.keys(this.flags()).length);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/features`;
  }

  /** Fetch the effective flag map. Call once after auth from APP_INITIALIZER. */
  async load(): Promise<void> {
    if (!this.auth.isAuthenticated()) {
      return;
    }
    try {
      const res = await firstValueFrom(
        this.http.get<{ features: Record<string, boolean> }>(this.baseUrl),
      );
      this.flags.set(res?.features ?? {});
    } catch {
      // Offline / first run — leave everything enabled (empty map = all on).
      this.flags.set({});
    }
  }

  /** True unless a known key is explicitly disabled. Undefined key = enabled. */
  enabled(key?: string | null): boolean {
    if (!key) return true;
    return this.flags()[key] !== false;
  }

  /** Admin: persist the override map and refresh local state. */
  async save(flags: Record<string, boolean>): Promise<void> {
    const res = await firstValueFrom(
      this.http.put<{ features: Record<string, boolean> }>(
        `${this.config.apiUrl}/admin/features`,
        { flags },
      ),
    );
    this.flags.set(res?.features ?? flags);
  }

  /** Admin: full (unfiltered) model catalog + the current allow-list. */
  async modelSelection(): Promise<ModelSelectionState> {
    const res = await firstValueFrom(
      this.http.get<ModelSelectionState>(`${this.config.apiUrl}/admin/model-selection`),
    );
    return {
      models: res?.models ?? [],
      providers: res?.providers ?? [],
      selected: res?.selected ?? [],
    };
  }

  /** Admin: grouped, secret-free snapshot of the runtime (env) configuration. */
  async runtimeConfig(): Promise<ConfigGroup[]> {
    const res = await firstValueFrom(
      this.http.get<{ groups: ConfigGroup[] }>(`${this.config.apiUrl}/admin/config`),
    );
    return res?.groups ?? [];
  }

  /** Admin: persist the model allow-list (empty array = no restriction). */
  async saveModelSelection(models: string[]): Promise<string[]> {
    const res = await firstValueFrom(
      this.http.put<{ selected: string[] }>(`${this.config.apiUrl}/admin/model-selection`, {
        models,
      }),
    );
    return res?.selected ?? models;
  }

  /** Admin: named LLM failover chains (Phase 31.c), tried in order by
   *  llm.completion / llm.agent workflow nodes on a call failure. */
  async modelFailoverChains(): Promise<Record<string, string[]>> {
    const res = await firstValueFrom(
      this.http.get<{ chains: Record<string, string[]> }>(`${this.config.apiUrl}/admin/model-failover-chains`),
    );
    return res?.chains ?? {};
  }

  async saveModelFailoverChains(chains: Record<string, string[]>): Promise<Record<string, string[]>> {
    const res = await firstValueFrom(
      this.http.put<{ chains: Record<string, string[]> }>(
        `${this.config.apiUrl}/admin/model-failover-chains`,
        { chains },
      ),
    );
    return res?.chains ?? chains;
  }
}
