# Internationalisierung (i18n)

**Was es macht.** SpiceSibyl spricht fünf Sprachen über beide Kanäle — **Englisch, Französisch, Deutsch, Italienisch und Spanisch** — mit einem Runtime-Sprachumschalter in der Web-Konsole und einer Sprache pro Chat im Telegram-Bot. Im Web erfordert der Sprachwechsel weder Neukompilierung noch Neuladen der Seite.

> Versionen: [English](../en/internationalization.md) · [Italiano](../it/internazionalizzazione.md)

## Web-Konsole

- **Sprachumschalter.** Eine 🌐-Schaltfläche in der Navbar (neben den Design-/Akzent-Steuerungen) öffnet ein Menü der fünf Sprachen; die aktive ist hervorgehoben. Der Wechsel zeichnet die Oberfläche sofort neu.
- **Automatische Erkennung.** Beim ersten Besuch — wenn der Benutzer noch keine Sprache gewählt hat — wird die Browsersprache (`navigator.languages`) mit den unterstützten verglichen; passt keine, wird der historische Standard (**Italienisch**) verwendet, was das ursprüngliche Verhalten für bestehende Benutzer bewahrt.
- **Persistenz.** Die Wahl liegt im `localStorage` (sofort, offline) **und** auf dem aktiven Profil via `PATCH /api/v1/profiles/{id}` (`{ "locale": "de" }`), sodass sie dem Benutzer über Geräte folgt. Die auf dem Profil gespeicherte Sprache wird bei Login/Auswahl übernommen, *außer* der Browser enthält bereits eine explizite lokale Wahl.
- **Abdeckung.** Navbar und Menüs, die Sprache-/Design-/Akzent-Tooltips, die Chat-Ladeindikatoren, die Seiten (Anbieter, Statistiken, Werkzeuge, Workflows, MCP, Arbeitsbereiche usw.), der Profil-Dialog, die Toasts und die Onboarding-Tour sind lokalisiert. Spracheingabe (Web Speech API) und TTS folgen dem BCP-47-Tag der aktiven Sprache (z. B. `de-DE`) statt des vorherigen fest verdrahteten `it-IT`.

### Architektur

Eine leichte, abhängigkeitsfreie Runtime-i18n-Schicht (im minimalistischen Stil des Projekts — siehe das Telegram-Katalog und den SDK-losen MCP-Client):

| Komponente | Datei |
|------------|-------|
| Sprach-Metadaten (Codes, native Labels, BCP-47-Tags) | [`core/i18n/locale.ts`](../../frontend/src/app/core/i18n/locale.ts) |
| Kataloge (eine flache `Schlüssel → String`-Map pro Sprache) | [`core/i18n/translations/*.ts`](../../frontend/src/app/core/i18n/translations/) |
| `I18nService` (Signal der aktiven Sprache, Erkennung, Persistenz, `translate()`, Formatierer) | [`core/i18n/i18n.service.ts`](../../frontend/src/app/core/i18n/i18n.service.ts) |
| `TranslatePipe` (`\| t`) — impure, um auf Sprachwechsel zu reagieren | [`core/i18n/translate.pipe.ts`](../../frontend/src/app/core/i18n/translate.pipe.ts) |
| Lokalisierte Formatierungs-Pipes (`\| localeNumber`, `\| localeCost`, `\| localeDate`) | [`core/i18n/format.pipes.ts`](../../frontend/src/app/core/i18n/format.pipes.ts) |

Verwendung in einem Template:

```html
{{ 'nav.chat' | t }}
{{ 'chat.providerSwitch' | t: { from: 'groq', to: 'ollama' } }}   <!-- die {placeholders} werden aus params gefüllt -->
{{ message.estimated_cost | localeCost:6 }}
```

Auflösungsreihenfolge eines Schlüssels: **Katalog der aktiven Sprache → Standardkatalog (`it`) → der Schlüssel selbst.** Sprache hinzufügen = Katalogdatei anlegen, in `I18nService.CATALOGS` registrieren und einen Eintrag zu `SUPPORTED_LOCALES` hinzufügen.

### Lokalisierte Formatierung

Zahlen, Kosten und Daten werden gemäß der aktiven Sprache über die `Intl`-API gerendert, geschlüsselt auf das BCP-47-Tag der Sprache:

- `localeNumber` → `Intl.NumberFormat` (Tausender- und Dezimaltrennzeichen)
- `localeCost` → `Intl.NumberFormat` mit `style: 'currency', currency: 'USD'` (Symbolposition je Sprache: `$1.23` vs `1,23 $`)
- `localeDate` → `Intl.DateTimeFormat`

## Telegram-Bot

- **Sprache pro Chat.** `/lang` zeigt eine Inline-Tastatur mit den fünf Sprachen; `/lang en|fr|de|es|it` setzt sie direkt. Die Wahl persistiert in `telegram_prefs` und wird beim Start vorgeladen.
- **Katalog.** Die gesamte Befehlsausgabe, Inline-Tastaturen, Schnellaktionen, Erinnerungen und Fehlermeldungen liegen in [`backend/app/telegram/i18n.py`](../../backend/app/telegram/i18n.py) (`MESSAGES[locale][key]`), mit Fallback-Kette `locale → Standard (it) → Schlüssel`.
- **Lokalisierte Bestätigungen.** Erinnerungszeiten nutzen eine lokalisierte Datumsreihenfolge (Englisch nutzt Monat/Tag, die anderen Sprachen Tag/Monat) in der `TIMEZONE` des Chats.

## Dokumentation und Screenshots

Die Dokumente und ihre Screenshots sind pro Sprache: `docs/<lang>/*.md` +
`docs/<lang>/screenshots/*.png` für jede von `en`, `it`, `fr`, `de`, `es`. Alle fünf
Sprachsätze sind vollständig übersetzt (passend zur *App-UI*, die in allen fünf
lokalisiert ist); Screenshots werden pro Sprache in der aktiven UI-Sprache aufgenommen.

- **In-App-Hilfe** (`/help`) lädt das Dokumentset passend zur aktiven UI-Sprache,
  mit Rückfall auf Englisch, falls ein Set fehlt; Screenshots verweisen auf
  `docs/<lang>/screenshots/`. Die Veröffentlichung erfolgt zur Build-Zeit durch
  [`frontend/scripts/copy-docs.mjs`](../../frontend/scripts/copy-docs.mjs) (fünf Sprachen).
- **Screenshots werden mit Playwright** gegen eine laufende Instanz von
  [`frontend/scripts/screenshots.mjs`](../../frontend/scripts/screenshots.mjs) erzeugt:
  es meldet sich an, wechselt die UI-Sprache und erfasst jede Seite in `docs/<lang>/screenshots/`.

  ```bash
  # die App muss laufen (Standard http://localhost:8888)
  ADMIN_EMAIL=… ADMIN_PASSWORD=… node frontend/scripts/screenshots.mjs        # alle 5 Sprachen
  node frontend/scripts/screenshots.mjs de es                                 # eine Teilmenge
  ```

## Konfiguration

Keine Konfiguration nötig — alle fünf Sprachen sind in der App enthalten. Die historische Standardsprache ist Italienisch; ändere sie über `DEFAULT_LOCALE` in `core/i18n/locale.ts` (web) und `i18n.py` (Telegram).
