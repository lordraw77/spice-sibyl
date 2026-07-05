# Telegram-Bot

**Was es macht.** Ein Polling-Bot, der das Gateway auf Telegram bereitstellt: Verlauf pro Chat, Streaming-Antworten mit Live-Bearbeitung der Nachricht, Modellauswahl, Vision, Bildgenerierung, Sprachtranskription, Dokumente, persönliches Gedächtnis, **Wissensdatenbank (RAG)**, Erinnerungen und Web-Profil-Verknüpfung.

**Konfiguration.** `TELEGRAM_BOT_TOKEN` in `backend/.env`; optionale Allowlist mit `TELEGRAM_ALLOWED_USERS` (kommagetrennte User-Ids). Zeitzone der Erinnerungen mit `TIMEZONE` (Standard `Europe/Rome`). Benötigt das Extra `python-telegram-bot[job-queue]` für Erinnerungen.

## Befehle

| Befehl | Was er macht |
|--------|--------------|
| `/start` | Begrüßungsnachricht |
| `/new` | neue Unterhaltung (setzt den Chat-Kontext zurück) |
| `/model` | Modellauswahl über eine **zweistufige Inline-Tastatur** (Anbieter → Modell, mit Zurück-Navigation und ✅ am aktuellen Modell) |
| `/models` | listet verfügbare Modelle |
| `/agent` · `/chat` | wechselt zwischen Agentenmodus (Multi-MCP-Orchestrator) und normalem Chat |
| `/imagine <prompt>` | erzeugt ein Bild (`IMAGE_GENERATION_CHAIN`) und sendet es als Foto mit Anbieter/Modell-Beschriftung |
| `/history` | die letzten 20 Nachrichten der aktuellen Sitzung |
| `/search <suche>` | Volltextsuche (FTS5) über alle gespeicherten Unterhaltungen: Titel + Auszüge |
| `/link` · `/unlink` | erzeugt den Code zum Verknüpfen/Trennen des Web-Profils (siehe [Authentifizierung und Profile](authentication-and-profiles.md)) |
| `/remind` | Erinnerungen: `/remind 15:50 Backups prüfen` oder relativ `/remind +30m …`, `2h`, `1d` |
| `/reminders` · `/unremind <id>` | listet / storniert ausstehende Erinnerungen |
| `/memory on\|off\|list\|del <id>` | persönliches Gedächtnis über das verknüpfte Profil (siehe [Gedächtnis und Personalisierung](memory-and-personalization.md)) |
| `/kb list\|del <id>` | verwaltet die Wissensdatenbank des verknüpften Profils; zum Hinzufügen eine Datei mit **`/kb`-Bildunterschrift** senden (siehe unten) |
| `/rag on\|off` | schaltet die Wissensdatenbank-Injektion in diesem Chat um (pro Chat, **standardmäßig OFF**) |
| `/tool on\|off` | schaltet die Tool-Schleife für diesen Chat um (pro Chat, **standardmäßig OFF**) |
| `/tools` | listet die verfügbaren Tools (gruppiert) und den aktuellen Umschalter-Status auf — nur Anzeige, ändert den Zustand nicht |
| `/lang` · `/lang en\|it\|fr\|de\|es` | Bot-Sprache pro Chat (Inline-Tastatur oder direkt); persistiert in `telegram_prefs` |

## Medien

- **Fotos** an den Bot → werden vom aktiven Modell automatisch per Vision beschrieben.
- **Sprach-/Audionachrichten** → transkribiert mit Groq Whisper (`whisper-large-v3`); der Bot zeigt die Transkription und streamt dann die Antwort auf den transkribierten Text.
- **Dokumente** PDF / TXT / DOCX / MD → der Text wird extrahiert (auf 8.000 Zeichen gekürzt) und als **einmaliger** Kontext fürs Modell verwendet, zusammen mit einer etwaigen Bildunterschrift. Mit einer `/kb`-Bildunterschrift wird das Dokument stattdessen **in die Wissensdatenbank aufgenommen** (siehe unten).

## Wissensdatenbank (RAG)

Erweitert das RAG des Web-Profils (siehe [Wissensdatenbank](knowledge-rag.md)) auf den Telegram-Kanal. Erfordert ein **verknüpftes Web-Profil** (`/link`): jeder `/kb`/`/rag`-Befehl und jeder Upload mit `/kb`-Bildunterschrift fordert zum Verknüpfen auf, wenn kein Profil verbunden ist.

- **Aufnahme** — sende eine **PDF / TXT / DOCX / MD**-Datei mit `/kb`-Bildunterschrift: sie wird der Wissensdatenbank des verknüpften Profils hinzugefügt, mit derselben Pipeline wie Web-Uploads (`rag_service.ingest`: Extraktion → Chunking → Embedding) und sha256-Duplikaterkennung.
- **Verwaltung** — `/kb list` zeigt Dokumente mit Status-Icon (✅ bereit · ⏳ ausstehend · ⚠️ Fehler), 🔗 für URL-Dokumente und Chunk-Anzahl; `/kb del <id>` entfernt ein Dokument per Id-Präfix.
- **Abruf** — mit `/rag on` lässt `_stream_reply` bei jeder Nachricht die relevantesten Chunks abrufen (`rag_service.retrieve`, hybride Suche + optionales Rerank) und in die letzte Benutzernachricht einfügen; die Antwort erhält eine 📚-Quellen-Fußzeile (deduplizierte Dateinamen). Der Schalter ist **pro Chat**, persistiert in `telegram_prefs.rag` und wird beim Start neu geladen.

## Tools und MCP (Phase 23.b)

Bringt die **Tool-Schleife** des Web-Chats zu Telegram: mit `/tool on` beschränkt sich eine Completion nicht mehr aufs Streaming — der Bot fusioniert die integrierten Tools, die **benutzerdefinierten Tools** des verknüpften Profils und jedes erkannte **MCP-Tool** (`mcp__<server>__<tool>`, siehe [MCP](mcp.md)) in die Anfrage und führt die gemeinsame Server-seitige Schleife aus (`ChatService._stream_with_tools`), sodass das Verhalten über Kanäle hinweg identisch ist.

- **Umschalter** — `/tool on|off` schaltet die Tool-Schleife direkt um. **Pro Chat**, **standardmäßig OFF**, persistiert in `telegram_prefs.tools` und beim Start neu geladen (wie `/rag`). Profilbezogene Tools (`kb_search`, `create_reminder`, benutzerdefinierte Tools) werden beim verknüpften Profil aufgelöst.
- **Auflistung** — `/tools` listet die verfügbaren Tools gruppiert nach Art (🧩 integriert · 🔌 MCP · 🛠 benutzerdefiniert) zusammen mit dem aktuellen Umschalter-Status auf; das ist reine Anzeige und ändert den Zustand nie (nutze `/tool`, um ihn zu ändern).
- **Fortschritt** — Tool-Aufrufe erscheinen live in der Streaming-Antwort (⚙ *Tool-Name* während Ausführung, wird zu ✅ beim Ergebnis).
- **Erkennung** — MCP-Tools werden erneut sondiert, wenn Sie `/tools` ausführen (oder wenn der Cache kalt ist) und in `mcp_service` gespeichert, sodass normale Nachrichten nicht die Sondierungslatenz zahlen.
- **Agent-Modus** — `agent/*`-Modelle orchestrieren ihre eigenen Tools; der `/tool`-Umschalter gilt nicht für sie.

## Schnellaktionen

Inline-Buttons nach jeder Antwort: **Neu generieren** (wiederholt den letzten Zug), **Übersetzen** (IT↔EN), **Zusammenfassen** (Kernpunkte), **Fortsetzen**.

## Inline-Modus

`@bot_name Frage` in jedem Telegram-Chat: eine direkte Antwort ohne Streaming (max. 300 Tokens) als `InlineQueryResultArticle`, mit 30-Sekunden-Cache.

## Persistente Erinnerungen

Erinnerungen liegen in `telegram_reminders` und werden über die python-telegram-bot JobQueue geplant: sie **überleben Neustarts** (beim Boot neu geladen). Zeiten nutzen `TIMEZONE`, unabhängig von der Container-Uhr.
