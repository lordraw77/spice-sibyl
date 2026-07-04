/**
 * i18n locale definitions (Phase 22).
 *
 * Five supported locales, kept in sync with the Telegram bot catalog
 * (backend/app/telegram/i18n.py).  `it` is the historical default to preserve
 * the original UI behaviour for existing users; first-time visitors get their
 * browser language auto-detected (see I18nService).
 */

export type Locale = 'en' | 'it' | 'fr' | 'de' | 'es';

export const DEFAULT_LOCALE: Locale = 'it';

export interface LocaleMeta {
  code: Locale;
  /** Native display label with flag, shown in the switcher. */
  label: string;
  /** BCP-47 tag used for Intl date/number/currency formatting (Phase 22.c). */
  bcp47: string;
}

export const SUPPORTED_LOCALES: LocaleMeta[] = [
  { code: 'it', label: '🇮🇹 Italiano', bcp47: 'it-IT' },
  { code: 'en', label: '🇬🇧 English', bcp47: 'en-GB' },
  { code: 'fr', label: '🇫🇷 Français', bcp47: 'fr-FR' },
  { code: 'de', label: '🇩🇪 Deutsch', bcp47: 'de-DE' },
  { code: 'es', label: '🇪🇸 Español', bcp47: 'es-ES' },
];

export const LOCALE_CODES: Locale[] = SUPPORTED_LOCALES.map((l) => l.code);

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (LOCALE_CODES as string[]).includes(value);
}

/** A flat translation catalog: dotted key → string (may contain {placeholders}). */
export type TranslationMap = Record<string, string>;
