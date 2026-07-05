# Gedächtnis und Personalisierung

Funktionen der Phase 19: persistentes Gedächtnis pro Profil, automatische Titel, Antwort-Cache, Antwort-Feedback und die Info-Seite.

## Persistentes Gedächtnis pro Profil

**Was es macht.** SpiceSibyl merkt sich Fakten über dich über Unterhaltungen hinweg (Vorlieben, persönliche Fakten, laufende Projekte, dauerhafte Anweisungen). Nach jedem persistierten Austausch extrahiert ein asynchroner, kostengünstiger LLM-Aufruf (`MEMORY_EXTRACTION_MODEL`, Standard = `DEFAULT_MODEL`) bemerkenswerte Informationen und konsolidiert sie in der Tabelle `profile_memories` (automatische Deduplizierung, begrenzt auf `MEMORY_MAX_ITEMS` Erinnerungen). Ist das Gedächtnis aktiv, werden aktivierte Erinnerungen in einen `<user_memory>`-Block kompaktiert, der an den System-Prompt angehängt wird (`MEMORY_MAX_CHARS` Zeichen-Budget, neueste zuerst).

**So wird es benutzt.**
- Eigene Seite **Gedächtnis 🧠** (`/memory`, **Ressourcen → Gedächtnis** in der Navbar, oder der Link *Verwalten →* neben dem Gedächtnis-Schalter in der Seitenleiste): Erinnerungsliste mit Kategorie (⭐ Vorliebe, 💡 Fakt, 📁 Projekt, 📌 Anweisung), manuelles Hinzufügen mit Kategorienwahl, pro Erinnerung aktivieren/deaktivieren oder löschen, **Alles vergessen**. Die Checkbox **automatische Erinnerungs-Extraktion (Profil)** — der Schalter auf *Profilebene* (OFF = keine Extraktion und keine Injektion für das ganze Profil) — befindet sich ebenfalls hier.
- Der Schalter **Gedächtnis ON/OFF** im Bereich **Funktionen** der Seitenleiste ist der *Pro-Chat*-Schalter (Inkognito): OFF = neue Anfragen nutzen und füttern das Gedächtnis nicht.
- Mit dem Gedächtnis personalisierte Antworten zeigen den Chip **🧠 Gedächtnis** unter der Nachricht.

**Von Telegram.** `/memory on|off` schaltet das Gedächtnis im aktuellen Chat um (persistiert in `telegram_prefs`); `/memory list` zeigt die Erinnerungen des über `/link` verknüpften Web-Profils; `/memory del <id>` vergisst eine. Injektion und Extraktion funktionieren nur für verknüpfte Benutzer.

**Konfiguration.**

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `MEMORY_ENABLED` | `true` | Globaler Funktionsschalter |
| `MEMORY_EXTRACTION_MODEL` | *(leer = `DEFAULT_MODEL`)* | Modell für den asynchronen Extraktionsaufruf |
| `MEMORY_MAX_CHARS` | `2000` | Zeichen-Budget des injizierten Blocks |
| `MEMORY_MAX_ITEMS` | `100` | Max. Erinnerungen pro Profil |

API: `GET/POST /v1/memories`, `PATCH/DELETE /v1/memories/{id}`, `DELETE /v1/memories` (alles vergessen), `GET/PUT /v1/memories/settings`.

## Automatische Titel (LLM-Auto-Titling)

**Was es macht.** Nach dem ersten persistierten Austausch einer Unterhaltung erzeugt eine Hintergrundaufgabe einen prägnanten Titel (max. 6 Wörter, in der Sprache der Unterhaltung) und ersetzt die alte Heuristik „erste 60 Zeichen der ersten Nachricht". Die Unterhaltungsliste aktualisiert sich einige Sekunden später von selbst.

**Konfiguration.** `AUTO_TITLE_ENABLED` (Standard `true`), `TITLE_MODEL` (leer = `MEMORY_EXTRACTION_MODEL`, dann `DEFAULT_MODEL`).

## Antwort-Cache

**Was es macht.** Fertige Antworten kommen in einen In-Memory-LRU-Cache, exakt geschlüsselt auf Modell + Nachrichten + Temperatur + Max-Tokens. Eine identische Anfrage innerhalb der TTL überspringt den Anbieter komplett: die Antwort wird in einem Rutsch mit dem Chip **⚡ Cache** und null Latenz wiedergegeben. Anfragen mit Tools, `agent/*`-Modellen und multimodalem Inhalt (Bilder) werden nie gecacht.

**Konfiguration.** `RESPONSE_CACHE_ENABLED` (Standard `true`), `RESPONSE_CACHE_TTL_SECONDS` (Standard `600`), `RESPONSE_CACHE_MAX_ENTRIES` (Standard `256`). Hit/Miss-Statistiken sind auf der **Info**-Seite sichtbar.

## Antwort-Feedback (👍/👎)

**Was es macht.** Jede persistierte Assistenten-Antwort kann mit Daumen hoch/runter bewertet werden (optionale Notiz bei 👎). Die Bewertungen speisen einen exportierbaren Datensatz für die Offline-Modellbewertung.

**So wird es benutzt.**
- Fahre über eine Antwort: 👍 und 👎 erscheinen bei den Aktionen. Erneutes Klicken des aktiven Icons löscht die Bewertung.
- Exportiere den Datensatz über `GET /v1/feedback/export`: jede bewertete Antwort wird mit dem erzeugenden Prompt gepaart (Nachrichten-Id, Modell, Anbieter, Bewertung, Notiz).
- Regressions-Harness: `backend/scripts/eval_regression.py` spielt 👍-bewertete Prompts erneut gegen das Gateway und meldet Antworten, die zu weit von den genehmigten abweichen.

```bash
python backend/scripts/eval_regression.py dataset.json \
  --base-url http://localhost:8800/api/v1 \
  --email admin@example.com --password ... [--model groq/llama-3.1-8b-instant]
```

## Info-Seite

**Was es macht.** Der Navbar-Eintrag **Info** öffnet eine Seite mit: Web-UI-Version (aus der `package.json` zur Build-Zeit), Backend-Version/Umgebung/Uptime (`GET /v1/info`), Standardmodell, Datenbank (Pfad und Größe), genutzte API-Endpoints (Basis-URL, Health, Readiness, Metriken, OpenAPI-Docs-Link), Live-READY/DEGRADED-Status und die Liste der aktivierten Funktionen mit Cache-Statistiken.

**Konfiguration.** Die Backend-Version kommt aus `APP_VERSION` (Standard an das Release angeglichen); Docker-Builds stempeln sie automatisch aus dem Release-Tag (`make release VERSION=v1.9.0`).
