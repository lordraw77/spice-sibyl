# Interfaccia e UX

## Tema scuro/chiaro e colore d'accento

**Cosa fa.** Sistema di temi basato su CSS custom properties (`--bg-primary`, `--text-primary`, `--accent`, …) con modalità dark / light / system e colore d'accento personalizzabile.

**Come si usa.**
- **Toggle tema**: icona sole/luna nella navbar; la preferenza è salvata in localStorage (`spicesibyl_theme`) e applicata via attributo `[data-theme]` su `<html>`.
- **Accent color**: picker nella navbar con 8 swatch preimpostati + selettore colore libero; aggiorna dinamicamente tutte le variabili `--accent-*` e funziona in entrambi i temi (`spicesibyl_accent`).

## Onboarding guidato

**Cosa fa.** Al primo accesso parte un tour guidato con overlay a "spotlight" sugli elementi chiave (selezione modello, tool, system prompt, comandi slash); su viewport stretti la card è centrata.

![Tour di onboarding](../screenshots/onboarding.png)

**Come si usa.** Segui i passi con **Avanti** o esci con **Salta**; il completamento è ricordato in localStorage (`spicesibyl_onboarded`). Il pulsante di replay nella topbar della chat lo riavvia in ogni momento.

## Scorciatoie da tastiera

| Scorciatoia | Azione |
|-------------|--------|
| `Ctrl+K` | ricerca conversazioni (apre la sidebar e mette il focus sulla barra di ricerca) |
| `Alt+N` | nuova chat |
| `Ctrl+Shift+S` | mostra/nascondi la sidebar |

Le scorciatoie non scattano mentre si scrive in un campo di input (eccetto `Ctrl+K`).

## Layout mobile

- Media query responsive: sidebar come overlay fisso con backdrop, chat e composer adattati agli schermi piccoli.
- **Swipe dal bordo** per aprire/chiudere la sidebar.
- Target touch ≥ 44 px; pulsanti export della topbar solo-icona; sotto i 575 px la navbar collassa in un menu hamburger.

## PWA (Progressive Web App)

**Cosa fa.** L'app è installabile (manifest con icone 192/512/maskable + apple-touch-icon) con service worker Angular attivo solo in produzione: shell dell'app disponibile offline.

**Notifiche di completamento.** Opt-in nel pannello **Parametri**: se una generazione dura più di 10 secondi e la scheda è in background, al termine arriva una notifica di sistema locale (nessun server push/VAPID).

**Come si installa.** Da Chrome/Edge: icona "installa" nella barra degli indirizzi; da mobile: «Aggiungi a schermata Home».

## Indicatori di caricamento

Barra di avanzamento animata sotto la topbar durante ogni richiesta, con colore/velocità legati alla fase: attesa del modello (ambra), esecuzione tool (blu, più veloce), streaming (standard). Le bolle di tool-call in attesa di risultato mostrano uno spinner al posto dell'icona ⚙.

## Gestione errori

Sistema di toast globale (ErrorInterceptor + NotificationService): errori HTTP e frame SSE `event: error` del backend diventano toast + messaggio nella bolla; i rate limit dei provider sono mappati su HTTP 429.
