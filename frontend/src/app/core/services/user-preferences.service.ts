import { Injectable } from '@angular/core';

const STORAGE_KEY = 'spicesibyl_preferences';

export interface UserPreferences {
  selectedModel: string | null;
  selectedProviders: string[];
  capabilityFilter: string;
  availabilityFilter: 'all' | 'free';
  temperature: number;
  maxTokens: number;
  toolsEnabled: boolean;
  ragEnabled: boolean;
  sidebarOpen: boolean;
  sectionsOpen: {
    conversations: boolean;
    model: boolean;
    provider: boolean;
    system: boolean;
    params: boolean;
    knowledge: boolean;
  };
}

const DEFAULTS: UserPreferences = {
  selectedModel: null,
  selectedProviders: [],
  capabilityFilter: 'all',
  availabilityFilter: 'all',
  temperature: 0.7,
  maxTokens: 0,
  toolsEnabled: false,
  ragEnabled: false,
  sidebarOpen: window.innerWidth >= 992,
  sectionsOpen: {
    conversations: true,
    model: true,
    provider: true,
    system: false,
    params: false,
    knowledge: false,
  },
};

@Injectable({ providedIn: 'root' })
export class UserPreferencesService {
  private prefs: UserPreferences;

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

  set<K extends keyof UserPreferences>(key: K, value: UserPreferences[K]): void {
    this.prefs[key] = value;
    this.persist();
  }

  setSection(section: keyof UserPreferences['sectionsOpen'], open: boolean): void {
    this.prefs.sectionsOpen[section] = open;
    this.persist();
  }
}
