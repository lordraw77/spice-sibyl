import { Injectable, computed, signal } from '@angular/core';

import {
  DEFAULT_LOCALE,
  Locale,
  LocaleMeta,
  SUPPORTED_LOCALES,
  TranslationMap,
  isLocale,
} from './locale';
import { CATALOGS } from './messages';

const STORAGE_KEY = 'spicesibyl_locale';

/**
 * Runtime UI internationalization (Phase 22.a).
 *
 * Lightweight, dependency-free (mirrors the project's minimalist style — see
 * the Telegram i18n module and the SDK-less MCP client). The active locale is a
 * signal; the impure TranslatePipe reads it so switching language re-renders the
 * whole app without a reload.
 *
 * Resolution order for a key: active-locale catalog → default-locale (it)
 * catalog → the key string itself. Placeholders use `{name}` and are filled
 * from the params object.
 *
 * Persistence: localStorage (immediate, offline). The profile's locale is also
 * synced to the backend by ProfileService consumers so the choice follows the
 * user across devices; on first visit with no stored choice the browser
 * language is auto-detected.
 */
@Injectable({ providedIn: 'root' })
export class I18nService {
  readonly locale = signal<Locale>(this.initialLocale());

  /** Metadata (label, bcp47) for the active locale — handy for formatting. */
  readonly localeMeta = computed<LocaleMeta>(
    () => SUPPORTED_LOCALES.find((l) => l.code === this.locale())!,
  );

  /** BCP-47 tag for Intl.* formatters (Phase 22.c). */
  readonly bcp47 = computed(() => this.localeMeta().bcp47);

  readonly supported = SUPPORTED_LOCALES;

  constructor() {
    this.applyDocumentLang();
  }

  /** Switch the active locale and persist it to localStorage. */
  setLocale(locale: Locale): void {
    if (!isLocale(locale) || locale === this.locale()) {
      if (isLocale(locale)) this.applyDocumentLang();
      return;
    }
    this.locale.set(locale);
    localStorage.setItem(STORAGE_KEY, locale);
    this.applyDocumentLang();
  }

  /**
   * Adopt a locale coming from a persisted profile (backend). Unlike setLocale
   * this does not overwrite an explicit local choice the user just made in this
   * session — it only applies when nothing is stored locally yet.
   */
  adoptProfileLocale(locale: string | null | undefined): void {
    if (!isLocale(locale)) return;
    if (localStorage.getItem(STORAGE_KEY)) return;
    this.setLocale(locale);
  }

  /** True when the user has explicitly chosen a locale (vs. browser-detected). */
  hasExplicitChoice(): boolean {
    return !!localStorage.getItem(STORAGE_KEY);
  }

  /**
   * Translate a key for the active locale, interpolating {placeholders}.
   * Reads the locale signal so callers inside a reactive context re-run.
   */
  translate(key: string, params?: Record<string, string | number>): string {
    const active = this.locale();
    const template =
      CATALOGS[active][key] ?? CATALOGS[DEFAULT_LOCALE][key] ?? key;
    if (!params) return template;
    return template.replace(/\{(\w+)\}/g, (_, name: string) =>
      name in params ? String(params[name]) : `{${name}}`,
    );
  }

  // ── Locale-aware formatting (Phase 22.c) ────────────────────────────────
  // Keyed on the active locale's BCP-47 tag via the Intl API. Reading the
  // locale signal keeps callers reactive when the language changes.

  /** Format an integer/float with locale grouping and decimal separators. */
  formatNumber(value: number, options?: Intl.NumberFormatOptions): string {
    return new Intl.NumberFormat(this.bcp47(), options).format(value);
  }

  /** Format a USD cost figure (currency symbol/placement follows the locale). */
  formatCost(value: number, fractionDigits = 4): string {
    return new Intl.NumberFormat(this.bcp47(), {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    }).format(value);
  }

  /** Format a Date / epoch-ms with locale date+time conventions. */
  formatDate(value: number | Date, options?: Intl.DateTimeFormatOptions): string {
    const date = typeof value === 'number' ? new Date(value) : value;
    return new Intl.DateTimeFormat(
      this.bcp47(),
      options ?? { dateStyle: 'medium', timeStyle: 'short' },
    ).format(date);
  }

  private initialLocale(): Locale {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isLocale(stored)) return stored;
    return this.detectBrowserLocale();
  }

  /** Best-effort browser-language auto-detection on first visit. */
  private detectBrowserLocale(): Locale {
    const candidates = [
      ...(navigator.languages ?? []),
      navigator.language,
    ].filter(Boolean);
    for (const tag of candidates) {
      const base = tag.slice(0, 2).toLowerCase();
      if (isLocale(base)) return base;
    }
    return DEFAULT_LOCALE;
  }

  private applyDocumentLang(): void {
    document.documentElement.setAttribute('lang', this.locale());
  }
}
