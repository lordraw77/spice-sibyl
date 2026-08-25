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
  templateUrl: './settings-page.component.html',
  styleUrls: ['./settings-page.component.css'],
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

  // --- LLM failover chains (Phase 31.c) ---
  readonly failoverChains = signal<{ name: string; models: string[] }[]>([]);
  /** Per-row "add model" picker draft (kept out of the row itself so picking
   *  doesn't need to touch/re-render the whole row array). */
  readonly failoverChainDraftModel = signal<string[]>([]);
  private baselineFailoverChains: Record<string, string[]> = {};

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

  readonly failoverChainsDirty = computed(() => {
    const current = this.chainsAsRecord(this.failoverChains());
    const baseline = this.baselineFailoverChains;
    const currentKeys = Object.keys(current);
    const baselineKeys = Object.keys(baseline);
    if (currentKeys.length !== baselineKeys.length) return true;
    return currentKeys.some((k) => JSON.stringify(current[k]) !== JSON.stringify(baseline[k]));
  });

  readonly dirty = computed(
    () => this.featuresDirty() || this.selectionDirty() || this.failoverChainsDirty(),
  );

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
      this.loadFailoverChains();
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
    if (id === 'catalog') return this.selectionDirty() || this.failoverChainsDirty();
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

  private loadFailoverChains(): void {
    this.features
      .modelFailoverChains()
      .then((chains) => {
        this.baselineFailoverChains = chains;
        const rows = Object.entries(chains).map(([name, models]) => ({ name, models: [...models] }));
        this.failoverChains.set(rows);
        this.failoverChainDraftModel.set(rows.map(() => ''));
      })
      .catch(() => {});
  }

  /** {name, models} rows → the {name: [modelId, ...]} shape the API expects,
   *  dropping blank names/rows. */
  private chainsAsRecord(rows: { name: string; models: string[] }[]): Record<string, string[]> {
    const out: Record<string, string[]> = {};
    for (const row of rows) {
      const name = row.name.trim();
      if (!name || !row.models.length) continue;
      out[name] = row.models;
    }
    return out;
  }

  /** Stable per-row identity for *ngFor so editing one row's fields doesn't
   *  recreate every row's DOM (which would drop input focus on each keystroke). */
  trackByIndex(index: number): number {
    return index;
  }

  addFailoverChain(): void {
    this.failoverChains.update((rows) => [...rows, { name: '', models: [] }]);
    this.failoverChainDraftModel.update((drafts) => [...drafts, '']);
  }

  removeFailoverChain(index: number): void {
    this.failoverChains.update((rows) => rows.filter((_, i) => i !== index));
    this.failoverChainDraftModel.update((drafts) => drafts.filter((_, i) => i !== index));
  }

  updateFailoverChainName(index: number, name: string): void {
    this.failoverChains.update((rows) => rows.map((r, i) => (i === index ? { ...r, name } : r)));
  }

  /** Model ids already used in a chain, offered as options to add next. */
  availableModelsForChain(index: number): CatalogModel[] {
    const used = new Set(this.failoverChains()[index]?.models ?? []);
    return this.catalog().filter((m) => !used.has(m.id));
  }

  setDraftModel(index: number, modelId: string): void {
    this.failoverChainDraftModel.update((drafts) => drafts.map((d, i) => (i === index ? modelId : d)));
  }

  addModelToChain(index: number): void {
    const modelId = this.failoverChainDraftModel()[index];
    if (!modelId) return;
    this.failoverChains.update((rows) =>
      rows.map((r, i) => (i === index && !r.models.includes(modelId) ? { ...r, models: [...r.models, modelId] } : r)),
    );
    this.setDraftModel(index, '');
  }

  removeModelFromChain(index: number, modelId: string): void {
    this.failoverChains.update((rows) =>
      rows.map((r, i) => (i === index ? { ...r, models: r.models.filter((m) => m !== modelId) } : r)),
    );
  }

  modelLabel(modelId: string): string {
    return this.catalog().find((m) => m.id === modelId)?.label || modelId;
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
    const rows = Object.entries(this.baselineFailoverChains).map(([name, models]) => ({
      name,
      models: [...models],
    }));
    this.failoverChains.set(rows);
    this.failoverChainDraftModel.set(rows.map(() => ''));
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
    if (this.failoverChainsDirty()) {
      const chains = this.chainsAsRecord(this.failoverChains());
      jobs.push(
        this.features.saveModelFailoverChains(chains).then(() => {
          this.baselineFailoverChains = chains;
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
