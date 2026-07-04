# Internazionalizzazione (i18n)

**Cosa fa.** SpiceSibyl parla cinque lingue su entrambi i canali — **inglese, francese, tedesco, italiano e spagnolo** — con un selettore di lingua a runtime nella console web e una lingua per-chat nel bot Telegram. Sul web il cambio di lingua non richiede ricompilazione né ricaricamento della pagina.

> 🇬🇧 English version: [internationalization.md](../en/internationalization.md)

## Console web

- **Selettore di lingua.** Un pulsante 🌐 nella navbar (accanto ai controlli tema/accento) apre un menu con le cinque lingue; quella attiva è evidenziata. Il cambio ridisegna l'interfaccia all'istante.
- **Rilevamento automatico.** Alla prima visita — quando l'utente non ha ancora scelto una lingua — la lingua del browser (`navigator.languages`) viene confrontata con quelle supportate; se nessuna corrisponde si usa il default storico (**italiano**), preservando il comportamento originale per gli utenti esistenti.
- **Persistenza.** La scelta è salvata in `localStorage` (immediata, offline) **e** sul profilo attivo tramite `PATCH /api/v1/profiles/{id}` (`{ "locale": "fr" }`), così segue l'utente tra dispositivi. La lingua salvata sul profilo viene adottata al login/selezione *a meno che* il browser non contenga già una scelta esplicita locale.
- **Copertura.** Navbar e menu, i tooltip lingua/tema/accento, gli indicatori di caricamento della chat (attesa modello / esecuzione tool / streaming), il tour di onboarding e le azioni comuni sono localizzati. L'input vocale (Web Speech API) e il TTS seguono il tag BCP-47 della lingua attiva (es. `fr-FR`) invece del precedente `it-IT` fisso.

### Architettura

Un livello i18n a runtime leggero e senza dipendenze (in linea con lo stile minimalista del progetto — vedi il catalogo Telegram e il client MCP senza SDK):

| Componente | File |
|-----------|------|
| Metadati lingua (codici, etichette native, tag BCP-47) | [`core/i18n/locale.ts`](../../frontend/src/app/core/i18n/locale.ts) |
| Cataloghi (una mappa piatta `chiave → stringa` per lingua) | [`core/i18n/translations/*.ts`](../../frontend/src/app/core/i18n/translations/) |
| `I18nService` (signal della lingua attiva, rilevamento, persistenza, `translate()`, formattatori) | [`core/i18n/i18n.service.ts`](../../frontend/src/app/core/i18n/i18n.service.ts) |
| `TranslatePipe` (`\| t`) — impura, così reagisce ai cambi di lingua | [`core/i18n/translate.pipe.ts`](../../frontend/src/app/core/i18n/translate.pipe.ts) |
| Pipe di formattazione localizzata (`\| localeNumber`, `\| localeCost`, `\| localeDate`) | [`core/i18n/format.pipes.ts`](../../frontend/src/app/core/i18n/format.pipes.ts) |

Uso in un template:

```html
{{ 'nav.chat' | t }}
{{ 'lang.set' | t: { label: 'Français' } }}   <!-- i {placeholders} sono riempiti dai params -->
{{ message.estimated_cost | localeCost:6 }}
```

Ordine di risoluzione di una chiave: **catalogo lingua attiva → catalogo default (`it`) → la chiave stessa.** Aggiungere una lingua = aggiungere un file catalogo, registrarlo in `I18nService.CATALOGS` e aggiungere una voce a `SUPPORTED_LOCALES`.

### Formattazione localizzata

Numeri, costi e date sono resi secondo la lingua attiva tramite l'API `Intl` con il tag BCP-47 della lingua:

- `localeNumber` → `Intl.NumberFormat` (separatori di migliaia e decimali)
- `localeCost` → `Intl.NumberFormat` con `style: 'currency', currency: 'USD'` (posizione del simbolo secondo la lingua: `$1.23` vs `1,23 $`)
- `localeDate` → `Intl.DateTimeFormat`

## Bot Telegram

- **Lingua per-chat.** `/lang` mostra una tastiera inline con tutte e cinque le lingue; `/lang en|fr|de|es|it` la imposta direttamente. La scelta persiste in `telegram_prefs` ed è pre-caricata in cache all'avvio.
- **Catalogo.** Tutto l'output dei comandi, le tastiere inline, le azioni rapide, i promemoria e i messaggi di errore vivono in [`backend/app/telegram/i18n.py`](../../backend/app/telegram/i18n.py) (`MESSAGES[locale][key]`), con catena di fallback `locale → default (it) → chiave`.
- **Conferme localizzate.** Gli orari dei promemoria usano un ordine di data localizzato (l'inglese usa mese/giorno, le altre lingue giorno/mese) nel `TIMEZONE` della chat.

## Documentazione e screenshot

I documenti delle funzionalità e i relativi screenshot sono per-lingua:
`docs/<lang>/*.md` + `docs/<lang>/screenshots/*.png` per ciascuna di `en`, `it`,
`fr`, `de`, `es`. Inglese e italiano sono scritti a mano; `fr`/`de`/`es` per ora
sono impalcature in inglese (banner 🚧 nel README) in attesa di traduzione,
mentre la *UI dell'app* è già completamente localizzata in tutte e cinque.

- **Guida in-app** (`/help`) carica il set di documenti corrispondente alla
  lingua attiva, con fallback all'inglese se un set manca; gli screenshot puntano
  a `docs/<lang>/screenshots/`. La pubblicazione avviene in fase di build tramite
  [`frontend/scripts/copy-docs.mjs`](../../frontend/scripts/copy-docs.mjs) (cinque lingue).
- **Gli screenshot sono generati con Playwright** su un'istanza in esecuzione da
  [`frontend/scripts/screenshots.mjs`](../../frontend/scripts/screenshots.mjs): effettua il
  login, cambia la lingua della UI e cattura ogni pagina in `docs/<lang>/screenshots/`.

  ```bash
  # l'app deve essere in esecuzione (default http://localhost:8888)
  ADMIN_EMAIL=… ADMIN_PASSWORD=… node frontend/scripts/screenshots.mjs        # tutte e 5
  node frontend/scripts/screenshots.mjs de es                                 # un sottoinsieme
  ```

## Configurazione

Non è richiesta alcuna configurazione — tutte e cinque le lingue sono incluse. La lingua di default storica è l'italiano; per cambiarla modifica `DEFAULT_LOCALE` in `core/i18n/locale.ts` (web) e `i18n.py` (Telegram).
