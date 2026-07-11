import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import {
  FeatureService,
  FEATURE_KEYS,
  FeatureKey,
  CatalogModel,
  CatalogProvider,
  ConfigGroup,
} from '../../core/services/feature.service';
import { AuthService } from '../../core/services/auth.service';
import { NotificationService } from '../../core/services/notification.service';
import {
  NotificationPrefsService,
  NotifyEventType,
} from '../../core/services/notification-prefs.service';
import { ReminderPrefsService } from '../../core/services/reminder-prefs.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

interface FeatureRow {
  key: FeatureKey;
  labelKey: string; // reuses the navbar label
  descKey: string; // settings.feature.<key>.desc
}

/** Maps each toggleable feature to its existing navbar label + a description key. */
const LABELS: Record<FeatureKey, string> = {
  providers: 'nav.providers',
  discovery: 'nav.discovery',
  compare: 'nav.compare',
  stats: 'nav.stats',
  tools: 'nav.tools',
  workflows: 'nav.workflows',
  graph_workflows: 'nav.graphWorkflows',
  reminders: 'nav.reminders',
  mcp: 'nav.mcp',
  workspaces: 'nav.workspaces',
  templates: 'nav.templates',
  tags: 'nav.tags',
  knowledge: 'nav.knowledge',
  memory: 'nav.memory',
  help: 'nav.help',
  info: 'nav.info',
};

const row = (key: FeatureKey): FeatureRow => ({
  key,
  labelKey: LABELS[key],
  descKey: `settings.feature.${key}.desc`,
});

type TabId =
  | 'models'
  | 'tools'
  | 'resources'
  | 'info'
  | 'notifications'
  | 'timezone'
  | 'catalog'
  | 'config';

interface TabDef {
  id: TabId;
  labelKey: string;
  adminOnly: boolean;
}

const TABS: TabDef[] = [
  { id: 'models', labelKey: 'nav.group.models', adminOnly: true },
  { id: 'tools', labelKey: 'nav.group.tools', adminOnly: true },
  { id: 'resources', labelKey: 'nav.group.resources', adminOnly: true },
  { id: 'info', labelKey: 'nav.group.info', adminOnly: true },
  { id: 'notifications', labelKey: 'navbar.settings.notifications', adminOnly: false },
  { id: 'timezone', labelKey: 'settings.tab.timezone', adminOnly: false },
  { id: 'catalog', labelKey: 'settings.models.title', adminOnly: true },
  { id: 'config', labelKey: 'settings.tab.config', adminOnly: true },
];

/** Feature-toggle rows shown on each of the four feature tabs. */
const FEATURE_TABS: Partial<Record<TabId, FeatureRow[]>> = {
  models: (['providers', 'discovery', 'compare', 'stats'] as FeatureKey[]).map(row),
  tools: (['tools', 'workflows', 'graph_workflows', 'reminders', 'mcp', 'workspaces'] as FeatureKey[]).map(row),
  resources: (['templates', 'tags', 'knowledge', 'memory'] as FeatureKey[]).map(row),
  info: (['help', 'info'] as FeatureKey[]).map(row),
};

interface ProviderModels {
  provider: CatalogProvider;
  models: CatalogModel[];
}

/**
 * Settings console, organised in tabs:
 *  - Modelli / Strumenti / Risorse / Info (admin): feature toggles grouped by
 *    menu section (ON/OFF, FeatureService.save());
 *  - Notifiche: per-event cross-channel notification opt-ins (per-user, applied
 *    immediately — previously in the navbar gear popover);
 *  - Fuso orario: default reminder timezone (per-user, applied immediately);
 *  - Catalogo modelli (admin): allow-list of models offered by every model
 *    dropdown. Empty selection = all models visible; enforced by GET /v1/models.
 */
@Component({
  selector: 'app-settings-page',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe],
  template: `
    <div class="settings-page">
      <header class="page-head">
        <h1>{{ 'settings.title' | t }}</h1>
        <p class="subtitle">{{ 'settings.subtitle' | t }}</p>
      </header>

      <nav class="tabs" role="tablist">
        <button
          *ngFor="let tab of visibleTabs()"
          class="tab"
          role="tab"
          [class.active]="activeTab() === tab.id"
          [attr.aria-selected]="activeTab() === tab.id"
          (click)="selectTab(tab.id)"
        >
          {{ tab.labelKey | t }}
          <span class="dot" *ngIf="tabDirty(tab.id)"></span>
        </button>
      </nav>

      <!-- Feature-toggle tabs (models / tools / resources / info) -->
      <section class="group" *ngIf="featureRows() as rows">
        <ul class="feature-list">
          <li class="feature-row" *ngFor="let f of rows">
            <button
              class="toggle"
              [class.on]="draft()[f.key] !== false"
              (click)="toggle(f.key)"
              [attr.aria-pressed]="draft()[f.key] !== false"
            >
              {{ draft()[f.key] !== false ? 'ON' : 'OFF' }}
            </button>
            <div class="meta">
              <span class="name">{{ f.labelKey | t }}</span>
              <span class="desc">{{ f.descKey | t }}</span>
            </div>
          </li>
        </ul>
      </section>

      <!-- Notifications tab: per-user prefs, applied immediately -->
      <section class="group" *ngIf="activeTab() === 'notifications'">
        <p class="section-desc">{{ 'settings.notifications.desc' | t }}</p>
        <ul class="feature-list">
          <li class="feature-row" *ngFor="let evt of notifyEventTypes">
            <button
              class="toggle"
              [class.on]="notifyPrefsSvc.prefs()[evt.key]"
              (click)="toggleNotifyPref(evt.key)"
              [attr.aria-pressed]="notifyPrefsSvc.prefs()[evt.key]"
            >
              {{ notifyPrefsSvc.prefs()[evt.key] ? 'ON' : 'OFF' }}
            </button>
            <div class="meta">
              <span class="name">{{ evt.labelKey | t }}</span>
            </div>
          </li>
        </ul>
      </section>

      <!-- Timezone tab: per-user default reminder timezone, applied immediately -->
      <section class="group" *ngIf="activeTab() === 'timezone'">
        <p class="section-desc">{{ 'settings.timezone.desc' | t }}</p>
        <label class="tz-label">{{ 'reminders.timezoneDefault' | t }}</label>
        <select
          class="tz-select"
          [ngModel]="reminderPrefsSvc.timezone() ?? ''"
          (ngModelChange)="onReminderTimezoneChange($event)"
        >
          <option value="">{{ 'reminders.timezoneDeviceDefault' | t }}</option>
          <option *ngFor="let tz of timezoneOptions" [value]="tz">{{ tz }}</option>
        </select>
      </section>

      <!-- Model catalog allow-list tab (admin) -->
      <section class="group models-section" *ngIf="activeTab() === 'catalog'">
        <p class="section-desc">{{ 'settings.models.desc' | t }}</p>

        <div class="models-toolbar">
          <input
            class="search"
            type="text"
            [ngModel]="modelSearch()"
            (ngModelChange)="modelSearch.set($event)"
            [placeholder]="'settings.models.search' | t"
          />
          <span class="count" [class.unrestricted]="selectedCount() === 0">
            {{
              selectedCount() === 0
                ? ('settings.models.allVisible' | t)
                : selectedCount() + ' / ' + catalog().length + ' ' + ('settings.models.selected' | t)
            }}
          </span>
          <button class="btn ghost small" (click)="clearSelection()" [disabled]="selectedCount() === 0">
            {{ 'settings.models.clear' | t }}
          </button>
        </div>

        <div class="provider-block" *ngFor="let g of providerGroups()">
          <div class="provider-head">
            <span class="provider-name">{{ g.provider.label || g.provider.id }}</span>
            <span class="provider-count">{{ selectedInProvider(g) }} / {{ g.models.length }}</span>
            <button class="btn ghost small" (click)="selectProvider(g, true)">
              {{ 'settings.models.all' | t }}
            </button>
            <button class="btn ghost small" (click)="selectProvider(g, false)">
              {{ 'settings.models.none' | t }}
            </button>
          </div>
          <ul class="model-list">
            <li class="model-row" *ngFor="let m of g.models">
              <label>
                <input
                  type="checkbox"
                  [checked]="isSelected(m.id)"
                  (change)="toggleModel(m.id)"
                />
                <span class="model-label">{{ m.label || m.id }}</span>
                <span class="model-id" *ngIf="m.label && m.label !== m.id">{{ m.id }}</span>
                <span class="badge" *ngIf="m.free">free</span>
              </label>
            </li>
          </ul>
        </div>

        <p class="section-desc" *ngIf="!catalogLoaded()">{{ 'settings.models.loading' | t }}</p>
        <p class="section-desc" *ngIf="catalogLoaded() && catalog().length === 0">
          {{ 'settings.models.empty' | t }}
        </p>
      </section>

      <!-- Runtime configuration tab (admin, read-only) -->
      <section class="group" *ngIf="activeTab() === 'config'">
        <p class="section-desc">{{ 'settings.config.desc' | t }}</p>

        <div class="config-block" *ngFor="let g of configGroups()">
          <div class="config-head">{{ g.label }}</div>
          <ul class="config-list">
            <li class="config-row" *ngFor="let e of g.entries">
              <div class="config-top">
                <span class="config-key">{{ e.key }}</span>
                <span class="source-badge" [class.env]="e.configured">
                  {{ (e.configured ? 'settings.config.configured' : 'settings.config.default') | t }}
                </span>
              </div>
              <p class="config-desc">{{ e.description }}</p>
              <div class="config-values">
                <span class="config-value" [class.empty]="!e.value">
                  {{ e.value || ('settings.config.unset' | t) }}
                </span>
                <span class="config-default" *ngIf="e.configured && e.default !== e.value">
                  {{ 'settings.config.defaultLabel' | t }}
                  {{ e.default || ('settings.config.unset' | t) }}
                </span>
              </div>
            </li>
          </ul>
        </div>

        <p class="section-desc" *ngIf="!configLoaded()">{{ 'settings.config.loading' | t }}</p>
      </section>

      <footer class="actions" *ngIf="showFooter()">
        <button class="btn ghost" (click)="reset()" [disabled]="!dirty() || saving()">
          {{ 'settings.reset' | t }}
        </button>
        <button class="btn primary" (click)="save()" [disabled]="!dirty() || saving()">
          {{ saving() ? ('settings.saving' | t) : ('settings.save' | t) }}
        </button>
      </footer>
    </div>
  `,
  styles: [
    `
      .settings-page { max-width: 820px; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
      .page-head h1 { margin: 0 0 0.25rem; font-size: 1.4rem; }
      .subtitle { margin: 0 0 1.25rem; color: var(--text-secondary); font-size: 0.9rem; }
      .tabs {
        display: flex; flex-wrap: wrap; gap: 0.25rem;
        border-bottom: 1px solid var(--border); margin-bottom: 1.25rem;
      }
      .tab {
        position: relative; display: inline-flex; align-items: center; gap: 0.35rem;
        padding: 0.5rem 0.9rem; border: none; background: transparent; cursor: pointer;
        color: var(--text-secondary); font-weight: 600; font-size: 0.85rem;
        border-bottom: 2px solid transparent; margin-bottom: -1px;
        transition: color 0.15s, border-color 0.15s;
      }
      .tab:hover { color: var(--text-primary); }
      .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
      .dot {
        width: 6px; height: 6px; border-radius: 999px; background: var(--accent);
      }
      .group { margin-bottom: 1.75rem; }
      .feature-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
      .feature-row {
        display: flex; align-items: flex-start; gap: 0.85rem;
        padding: 0.75rem 0.9rem; border: 1px solid var(--border); border-radius: 10px;
        background: var(--bg-secondary);
      }
      .toggle {
        flex: 0 0 auto; min-width: 3rem; padding: 0.3rem 0.5rem; border-radius: 999px;
        border: 1px solid var(--border); background: var(--bg-primary);
        color: var(--text-secondary); font-weight: 700; font-size: 0.72rem; cursor: pointer;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
      }
      .toggle.on { background: var(--accent); border-color: var(--accent); color: #fff; }
      .meta { display: flex; flex-direction: column; gap: 0.15rem; }
      .name { font-weight: 600; }
      .desc { color: var(--text-secondary); font-size: 0.82rem; line-height: 1.35; }
      .actions {
        position: sticky; bottom: 0; display: flex; justify-content: flex-end; gap: 0.6rem;
        padding: 0.9rem 0; background: linear-gradient(to top, var(--bg-primary) 70%, transparent);
      }
      .btn { padding: 0.5rem 1.1rem; border-radius: 8px; font-weight: 600; cursor: pointer; border: 1px solid var(--border); }
      .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
      .btn.ghost { background: transparent; color: var(--text-primary); }
      .btn:disabled { opacity: 0.5; cursor: default; }
      .btn.small { padding: 0.25rem 0.6rem; font-size: 0.75rem; }
      .section-desc { margin: 0 0 0.75rem; color: var(--text-secondary); font-size: 0.85rem; }
      .tz-label { display: block; font-weight: 600; font-size: 0.85rem; margin-bottom: 0.4rem; }
      .tz-select {
        width: 100%; max-width: 320px; padding: 0.45rem 0.7rem; border-radius: 8px;
        border: 1px solid var(--border); background: var(--bg-secondary); color: var(--text-primary);
        font-size: 0.85rem;
      }
      .models-toolbar { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.9rem; flex-wrap: wrap; }
      .search {
        flex: 1 1 220px; padding: 0.45rem 0.7rem; border-radius: 8px;
        border: 1px solid var(--border); background: var(--bg-secondary); color: var(--text-primary);
      }
      .count { font-size: 0.8rem; color: var(--text-secondary); white-space: nowrap; }
      .count.unrestricted { color: var(--accent); font-weight: 600; }
      .provider-block {
        border: 1px solid var(--border); border-radius: 10px; background: var(--bg-secondary);
        margin-bottom: 0.6rem; overflow: hidden;
      }
      .provider-head {
        display: flex; align-items: center; gap: 0.6rem;
        padding: 0.55rem 0.9rem; border-bottom: 1px solid var(--border);
      }
      .provider-name { font-weight: 600; flex: 1; text-transform: capitalize; }
      .provider-count { font-size: 0.75rem; color: var(--text-secondary); }
      .model-list {
        list-style: none; margin: 0; padding: 0.35rem 0.4rem;
        max-height: 16rem; overflow-y: auto;
      }
      .model-row label {
        display: flex; align-items: center; gap: 0.55rem;
        padding: 0.32rem 0.5rem; border-radius: 6px; cursor: pointer; font-size: 0.86rem;
      }
      .model-row label:hover { background: var(--bg-primary); }
      .model-label { font-weight: 500; }
      .model-id { color: var(--text-secondary); font-size: 0.75rem; overflow: hidden; text-overflow: ellipsis; }
      .badge {
        font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
        padding: 0.05rem 0.4rem; border-radius: 999px;
        border: 1px solid var(--accent); color: var(--accent);
      }
      .config-block {
        border: 1px solid var(--border); border-radius: 10px; background: var(--bg-secondary);
        margin-bottom: 0.6rem; overflow: hidden;
      }
      .config-head {
        padding: 0.55rem 0.9rem; border-bottom: 1px solid var(--border); font-weight: 600;
      }
      .config-list { list-style: none; margin: 0; padding: 0; }
      .config-row { padding: 0.55rem 0.9rem; }
      .config-row + .config-row { border-top: 1px solid var(--border); }
      .config-top { display: flex; align-items: center; gap: 0.55rem; flex-wrap: wrap; }
      .config-key { font-family: monospace; font-size: 0.82rem; font-weight: 600; }
      .source-badge {
        font-size: 0.62rem; font-weight: 700; text-transform: uppercase;
        padding: 0.05rem 0.45rem; border-radius: 999px;
        border: 1px solid var(--border); color: var(--text-secondary);
      }
      .source-badge.env { border-color: var(--accent); color: var(--accent); }
      .config-desc {
        margin: 0.15rem 0 0.25rem; color: var(--text-secondary);
        font-size: 0.78rem; line-height: 1.35;
      }
      .config-values {
        display: flex; align-items: baseline; gap: 0.7rem; flex-wrap: wrap;
        font-size: 0.82rem;
      }
      .config-value { font-family: monospace; word-break: break-all; }
      .config-value.empty { color: var(--text-secondary); font-style: italic; font-family: inherit; }
      .config-default {
        color: var(--text-secondary); font-size: 0.75rem; word-break: break-all;
      }
    `,
  ],
})
export class SettingsPageComponent implements OnInit {
  private readonly features = inject(FeatureService);
  private readonly auth = inject(AuthService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  readonly notifyPrefsSvc = inject(NotificationPrefsService);
  readonly reminderPrefsSvc = inject(ReminderPrefsService);

  readonly draft = signal<Record<string, boolean>>({});
  readonly saving = signal(false);

  readonly isAdmin = this.auth.hasRole('admin');
  readonly visibleTabs = signal<TabDef[]>(TABS.filter((t) => !t.adminOnly || this.isAdmin));
  readonly activeTab = signal<TabId>(this.isAdmin ? 'models' : 'notifications');

  /** Feature rows for the active tab, or null when it is not a feature tab. */
  readonly featureRows = computed<FeatureRow[] | null>(
    () => FEATURE_TABS[this.activeTab()] ?? null,
  );

  /** Per-event cross-channel notification opt-ins (Phase 23.c). */
  readonly notifyEventTypes: { key: NotifyEventType; labelKey: string }[] = [
    { key: 'workflowDone', labelKey: 'chat.notify.prefs.workflowDone' },
    { key: 'imageGenDone', labelKey: 'chat.notify.prefs.imageGenDone' },
    { key: 'longCompletionDone', labelKey: 'chat.notify.prefs.longCompletionDone' },
    { key: 'reminderFired', labelKey: 'chat.notify.prefs.reminderFired' },
    { key: 'kbIngested', labelKey: 'chat.notify.prefs.kbIngested' },
  ];

  /** Common IANA timezones offered for the reminder default-timezone override. */
  readonly timezoneOptions = [
    'UTC', 'Europe/Rome', 'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Madrid',
    'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
    'America/Sao_Paulo', 'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Kolkata', 'Asia/Dubai',
    'Australia/Sydney',
  ];

  // --- Runtime configuration (read-only, admin) ---
  readonly configGroups = signal<ConfigGroup[]>([]);
  readonly configLoaded = signal(false);

  // --- Model catalog allow-list ---
  readonly catalog = signal<CatalogModel[]>([]);
  readonly catalogProviders = signal<CatalogProvider[]>([]);
  readonly catalogLoaded = signal(false);
  readonly modelSearch = signal('');
  readonly selection = signal<Set<string>>(new Set());
  private baselineSelection = new Set<string>();

  /** Baseline (effective) map to compare against for the dirty check. */
  private baseline: Record<string, boolean> = {};

  readonly selectedCount = computed(() => this.selection().size);

  /** Catalog grouped by provider, filtered by the search box. */
  readonly providerGroups = computed<ProviderModels[]>(() => {
    const q = this.modelSearch().trim().toLowerCase();
    const models = this.catalog().filter(
      (m) => !q || (m.label || m.id).toLowerCase().includes(q) || m.id.toLowerCase().includes(q),
    );
    const byProvider = new Map<string, CatalogModel[]>();
    for (const m of models) {
      const key = m.provider || 'other';
      const list = byProvider.get(key);
      if (list) list.push(m);
      else byProvider.set(key, [m]);
    }
    const known = new Map(this.catalogProviders().map((p) => [p.id, p]));
    return Array.from(byProvider.entries())
      .map(([id, list]) => ({ provider: known.get(id) ?? { id }, models: list }))
      .sort((a, b) => a.provider.id.localeCompare(b.provider.id));
  });

  readonly featuresDirty = computed(() => {
    const d = this.draft();
    return FEATURE_KEYS.some((k) => (d[k] !== false) !== (this.baseline[k] !== false));
  });

  readonly selectionDirty = computed(() => {
    const s = this.selection();
    if (s.size !== this.baselineSelection.size) return true;
    for (const id of s) if (!this.baselineSelection.has(id)) return true;
    return false;
  });

  readonly dirty = computed(() => this.featuresDirty() || this.selectionDirty());

  /** Save/reset only apply to the admin draft tabs; the per-user tabs persist
   *  immediately and the config tab is read-only. */
  readonly showFooter = computed(() => {
    const tab = this.activeTab();
    return this.isAdmin && tab !== 'notifications' && tab !== 'timezone' && tab !== 'config';
  });

  ngOnInit(): void {
    const requested = this.route.snapshot.queryParamMap.get('tab') as TabId | null;
    if (requested && this.visibleTabs().some((t) => t.id === requested)) {
      this.activeTab.set(requested);
    }
    this.hydrateFromService();
    if (this.isAdmin) {
      this.loadCatalog();
      this.loadConfig();
    }
  }

  private loadConfig(): void {
    this.features
      .runtimeConfig()
      .then((groups) => this.configGroups.set(groups))
      .catch(() => {})
      .finally(() => this.configLoaded.set(true));
  }

  selectTab(id: TabId): void {
    this.activeTab.set(id);
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { tab: id },
      replaceUrl: true,
    });
  }

  /** Unsaved-changes marker on the tab labels. */
  tabDirty(id: TabId): boolean {
    if (id === 'catalog') return this.selectionDirty();
    const rows = FEATURE_TABS[id];
    if (!rows) return false;
    const d = this.draft();
    return rows.some((f) => (d[f.key] !== false) !== (this.baseline[f.key] !== false));
  }

  toggleNotifyPref(key: NotifyEventType): void {
    this.notifyPrefsSvc.set(key, !this.notifyPrefsSvc.prefs()[key]);
  }

  onReminderTimezoneChange(value: string): void {
    this.reminderPrefsSvc.set(value || null);
  }

  private loadCatalog(): void {
    this.features
      .modelSelection()
      .then((res) => {
        this.catalog.set(res.models);
        this.catalogProviders.set(res.providers);
        // Keep only ids that still exist in the catalog.
        const ids = new Set(res.models.map((m) => m.id));
        const selected = new Set(res.selected.filter((id) => ids.has(id)));
        this.baselineSelection = new Set(selected);
        this.selection.set(selected);
      })
      .catch(() => {})
      .finally(() => this.catalogLoaded.set(true));
  }

  private hydrateFromService(): void {
    const map: Record<string, boolean> = {};
    for (const k of FEATURE_KEYS) {
      map[k] = this.features.enabled(k);
    }
    this.baseline = { ...map };
    this.draft.set(map);
  }

  toggle(key: FeatureKey): void {
    this.draft.update((m) => ({ ...m, [key]: m[key] === false }));
  }

  reset(): void {
    this.draft.set({ ...this.baseline });
    this.selection.set(new Set(this.baselineSelection));
  }

  isSelected(id: string): boolean {
    return this.selection().has(id);
  }

  toggleModel(id: string): void {
    this.selection.update((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  selectProvider(group: ProviderModels, on: boolean): void {
    this.selection.update((s) => {
      const next = new Set(s);
      for (const m of group.models) {
        if (on) next.add(m.id);
        else next.delete(m.id);
      }
      return next;
    });
  }

  selectedInProvider(group: ProviderModels): number {
    const s = this.selection();
    return group.models.reduce((n, m) => n + (s.has(m.id) ? 1 : 0), 0);
  }

  clearSelection(): void {
    this.selection.set(new Set());
  }

  save(): void {
    if (!this.dirty() || this.saving()) return;
    this.saving.set(true);
    const jobs: Promise<unknown>[] = [];
    if (this.featuresDirty()) {
      // Persist only the disabled overrides; everything else defaults to enabled.
      jobs.push(this.features.save(this.draft()));
    }
    if (this.selectionDirty()) {
      const selected = Array.from(this.selection());
      jobs.push(
        this.features.saveModelSelection(selected).then(() => {
          this.baselineSelection = new Set(selected);
        }),
      );
    }
    Promise.all(jobs)
      .then(() => {
        this.notify.add('success', this.i18n.translate('settings.saved'));
        this.hydrateFromService();
      })
      .catch(() => this.notify.add('error', this.i18n.translate('settings.saveFailed')))
      .finally(() => this.saving.set(false));
  }
}
