# Internationalization (i18n)

**What it does.** SpiceSibyl speaks five languages across both channels — **English, French, German, Italian and Spanish** — with a runtime language switcher in the web console and a per-chat language in the Telegram bot. No rebuild or reload is needed to change language on the web.

> 🇮🇹 Versione italiana: [internazionalizzazione.md](../it/internazionalizzazione.md)

## Web console

- **Language switcher.** A 🌐 globe button in the navbar (next to the theme/accent controls) opens a menu of the five locales; the active one is highlighted. Switching re-renders the interface instantly.
- **Auto-detection.** On first visit — when the user has not yet chosen a language — the browser language (`navigator.languages`) is matched against the supported set; if none matches, the historical default (**Italian**) is used, preserving the original behaviour for existing users.
- **Persistence.** The choice is stored in `localStorage` (immediate, offline) **and** on the active profile via `PATCH /api/v1/profiles/{id}` (`{ "locale": "fr" }`), so it follows the user across devices. A profile's stored locale is adopted on login/selection *unless* the browser already holds an explicit local choice.
- **Coverage.** Navbar and menus, the language/theme/accent tooltips, chat loading indicators (model warm-up / tool execution / streaming), the onboarding tour, and common actions are localized. Voice input (Web Speech API) and TTS follow the active locale's BCP-47 tag (e.g. `fr-FR`) instead of the previous hardcoded `it-IT`.

### Architecture

A lightweight, dependency-free runtime i18n layer (mirrors the project's minimalist style — see the Telegram catalog and the SDK-less MCP client):

| Piece | File |
|-------|------|
| Locale metadata (codes, native labels, BCP-47 tags) | [`core/i18n/locale.ts`](../../frontend/src/app/core/i18n/locale.ts) |
| Catalogs (one flat `key → string` map per locale) | [`core/i18n/translations/*.ts`](../../frontend/src/app/core/i18n/translations/) |
| `I18nService` (active-locale signal, detection, persistence, `translate()`, formatters) | [`core/i18n/i18n.service.ts`](../../frontend/src/app/core/i18n/i18n.service.ts) |
| `TranslatePipe` (`\| t`) — impure so it reacts to locale changes | [`core/i18n/translate.pipe.ts`](../../frontend/src/app/core/i18n/translate.pipe.ts) |
| Locale-aware format pipes (`\| localeNumber`, `\| localeCost`, `\| localeDate`) | [`core/i18n/format.pipes.ts`](../../frontend/src/app/core/i18n/format.pipes.ts) |

Usage in a template:

```html
{{ 'nav.chat' | t }}
{{ 'lang.set' | t: { label: 'Français' } }}   <!-- {placeholders} filled from params -->
{{ message.estimated_cost | localeCost:6 }}
```

Resolution order for a key: **active-locale catalog → default-locale (`it`) catalog → the key string itself.** Adding a locale = add a catalog file, register it in `I18nService.CATALOGS`, and add an entry to `SUPPORTED_LOCALES`.

### Locale-aware formatting

Numbers, costs and dates render per the active locale via the `Intl` API keyed on the locale's BCP-47 tag:

- `localeNumber` → `Intl.NumberFormat` (grouping and decimal separators, e.g. `1.234` in de vs `1,234` in en)
- `localeCost` → `Intl.NumberFormat` with `style: 'currency', currency: 'USD'` (symbol placement follows the locale: `$1.23` vs `1,23 $`)
- `localeDate` → `Intl.DateTimeFormat`

## Telegram bot

- **Per-chat language.** `/lang` shows an inline keyboard with all five locales; `/lang en|fr|de|es|it` sets it directly. The choice persists in `telegram_prefs` and is warm-cached at boot.
- **Catalog.** All command output, inline keyboards, quick actions, reminders and error messages live in [`backend/app/telegram/i18n.py`](../../backend/app/telegram/i18n.py) (`MESSAGES[locale][key]`), with a fallback chain `locale → default (it) → key`.
- **Locale-aware confirmations.** Reminder times render with a locale-aware date order (English uses month/day, the others day/month) in the chat's `TIMEZONE`.

## Documentation & screenshots

The feature docs and their screenshots are per-language: `docs/<lang>/*.md` +
`docs/<lang>/screenshots/*.png` for each of `en`, `it`, `fr`, `de`, `es`. All five
language sets are fully translated (matching the *app UI*, which is localized in all
five); screenshots are captured per language in the active UI locale.

- **In-app Help** (`/help`) loads the doc set matching the active UI language,
  falling back to English if a set is missing; screenshots resolve to
  `docs/<lang>/screenshots/`. Publishing is done at build time by
  [`frontend/scripts/copy-docs.mjs`](../../frontend/scripts/copy-docs.mjs) (five languages).
- **Screenshots are generated with Playwright** against a running instance by
  [`frontend/scripts/screenshots.mjs`](../../frontend/scripts/screenshots.mjs): it logs in,
  switches the UI language, and captures each page into `docs/<lang>/screenshots/`.

  ```bash
  # app must be running (default http://localhost:8888)
  ADMIN_EMAIL=… ADMIN_PASSWORD=… node frontend/scripts/screenshots.mjs        # all 5 langs
  node frontend/scripts/screenshots.mjs de es                                 # a subset
  ```

## Configuration

No configuration is required — all five locales ship in the app. The historical default locale is Italian; change it by editing `DEFAULT_LOCALE` in `core/i18n/locale.ts` (web) and `i18n.py` (Telegram).
