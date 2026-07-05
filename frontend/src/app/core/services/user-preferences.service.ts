import { Injectable, signal } from '@angular/core';

const STORAGE_KEY = 'spicesibyl_preferences';

export interface UserPreferences {
  selectedModel: string | null;
  selectedProviders: string[];
  capabilityFilter: string;
  availabilityFilter: 'all' | 'free';
  temperature: number;
  maxTokens: number;
  maxToolIterations: number;
  toolsEnabled: boolean;
  ragEnabled: boolean;
  /** Model ids hidden from the chat model picker (curated per provider on /providers). */
  hiddenModels: string[];
  /** Phase 19: false = incognito chat (no memory injection/extraction) */
  memoryEnabled: boolean;
  /** Phase 23: chat system prompt (was the standalone 'spicesibyl_system_prompt' key). */
  systemPrompt: string;
  sidebarOpen: boolean;
  sectionsOpen: {
    model: boolean;
    system: boolean;
    params: boolean;
  };
}

const DEFAULTS: UserPreferences = {
  selectedModel: null,
  selectedProviders: [],
  capabilityFilter: 'all',
  availabilityFilter: 'all',
  temperature: 0.7,
  maxTokens: 0,
  maxToolIterations: 5,
  toolsEnabled: false,
  ragEnabled: false,
  hiddenModels: [],
  memoryEnabled: true,
  systemPrompt: '',
  sidebarOpen: window.innerWidth >= 992,
  sectionsOpen: {
    model: true,
    system: false,
    params: false,
  },
};

@Injectable({ providedIn: 'root' })
export class UserPreferencesService {
  private prefs: UserPreferences;

  /**
   * Bumped on every local mutation. SettingsSyncService observes this to persist
   * the active profile's chat preferences to the backend (roaming profile,
   * Phase 23). hydrate() deliberately does NOT bump it — restoring from the
   * server must not echo straight back as a save.
   */
  readonly revision = signal(0);

  constructor() {
    this.prefs = this.load();
  }

  private load(): UserPreferences {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return { ...DEFAULTS, sectionsOpen: { ...DEFAULTS.sectionsOpen } };
      const saved = JSON.parse(raw);
      return {
        ...DEFAULTS,
        ...saved,
        sectionsOpen: { ...DEFAULTS.sectionsOpen, ...(saved.sectionsOpen ?? {}) },
      };
    } catch {
      return { ...DEFAULTS, sectionsOpen: { ...DEFAULTS.sectionsOpen } };
    }
  }

  private persist(): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(this.prefs));
  }

  get(): UserPreferences {
    return this.prefs;
  }

  /** Deep-ish copy of the current preferences, for persisting to the backend. */
  snapshot(): UserPreferences {
    return { ...this.prefs, sectionsOpen: { ...this.prefs.sectionsOpen } };
  }

  set<K extends keyof UserPreferences>(key: K, value: UserPreferences[K]): void {
    this.prefs[key] = value;
    this.persist();
    this.revision.update((n) => n + 1);
  }

  setSection(section: keyof UserPreferences['sectionsOpen'], open: boolean): void {
    this.prefs.sectionsOpen[section] = open;
    this.persist();
    this.revision.update((n) => n + 1);
  }

  /**
   * Replace preferences with a blob restored from the backend (roaming profile).
   * Merged over defaults like load() so missing/newer keys stay sane. Does not
   * bump `revision` — this is an inbound restore, not a user edit.
   */
  hydrate(data: Partial<UserPreferences>): void {
    this.prefs = {
      ...DEFAULTS,
      ...this.prefs,
      ...data,
      sectionsOpen: {
        ...DEFAULTS.sectionsOpen,
        ...this.prefs.sectionsOpen,
        ...(data.sectionsOpen ?? {}),
      },
    };
    this.persist();
  }
}
