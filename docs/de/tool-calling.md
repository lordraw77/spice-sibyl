# Tool-Calling

## Serverseitige Ausführungsschleife

**Was es macht.** Mit dem Schalter **Tool calling ON** in der Seitenleiste stellt das Backend dem Modell die registrierten Tools bereit und führt angeforderte Aufrufe serverseitig aus, wobei die Ergebnisse in einer Schleife ans Modell zurückgehen (max. 5 Iterationen im Chat, konfigurierbar über `CHAT_MAX_TOOL_ITERATIONS`; für längere Schleifen siehe [Workflows](mcp-and-agents.md#persistente-workflows)). Aufrufe und Ergebnisse werden als SSE-Events `tool_call` / `tool_result` gestreamt und als eigene Blasen im Gespräch dargestellt; ausstehende Aufrufe zeigen einen Spinner.

**Liste der verfügbaren Tools:** `GET /api/v1/tools` (Vereinigung aus Integrierten + eigenen Tools des Profils + MCP). Der Schalter **Tool calling ON/OFF** liegt im Bereich **Funktionen** der Seitenleiste; Verwaltung und Übersicht der Tools sind auf der Seite **Werkzeuge** (Link *Verwalten →*).

## Integrierte Tools

| Tool | Was es macht |
|------|--------------|
| `get_datetime` | aktuelles Datum/Uhrzeit |
| `calculator` | wertet mathematische Ausdrücke aus |
| `web_search` | Websuche via DuckDuckGo (HTML-Scraping für reichhaltige Auszüge, mit Fallback auf die Instant-Answer-API) |
| `read_url` | ruft eine Webseite ab und liefert ihren Text (HTML entfernt, max. 4.000 Zeichen) |
| `python_exec` | sandboxter Code-Interpreter (siehe unten) |
| `kb_search` | agentisches RAG: fragt die Wissensdatenbank des Profils auf Wunsch des Modells ab |
| `search_conversations` | episodisches Gedächtnis: Volltextsuche (FTS5) über vergangene Unterhaltungen |
| `generate_image` | erzeugt ein Bild über die konfigurierte Anbieterkette; das Bild wird dem Benutzer gezeigt |
| `get_weather` | aktuelles Wetter + Vorhersage via Open-Meteo (kostenlos, ohne API-Schlüssel) |
| `fetch_rss` | die letzten N Einträge eines RSS-2.0-/Atom-Feeds |
| `create_reminder` | erstellt eine Telegram-Erinnerung für das verknüpfte Konto („erinnere mich morgen um 9…") |
| `extract_document` | lädt ein PDF/DOCX/TXT/MD von einer URL und liefert dessen Text, ohne KB-Aufnahme |
| `http_request` | generischer GET/POST-HTTP-Aufruf an öffentliche APIs (optionale Allowlist `HTTP_REQUEST_ALLOWED_DOMAINS`) |

**SSRF-Härtung.** `read_url`, `fetch_rss`, `extract_document` und `http_request` verweigern URLs, deren Host auf private/Loopback-/Link-Local-Adressen auflöst. `kb_search`, `search_conversations` und `create_reminder` arbeiten automatisch auf dem Profil des Aufrufers.

## Eigene Tools (HTTP)

**Was es macht.** Registriere HTTP-Tools über die UI, ohne den Code anzufassen: Name, Beschreibung, Parameter (JSON Schema), URL/Methode/Header, Authentifizierung (keine / Bearer / eigener Header), Timeout. Sie werden pro Profil in der Tabelle `custom_tools` gespeichert und unter dem Namensraum `custom__<name>` in die Chat-Schleife injiziert.

![Werkzeuge-Seite](screenshots/tools.png)

**So wird es benutzt.**
1. Seite **Werkzeuge** → **Neues Tool**.
2. Fülle das Formular aus (Name, Beschreibung, Parameter-JSON-Schema, Endpoint, Auth, Timeout) und speichere.
3. Nutze das **integrierte Test-Panel** für einen Probeaufruf vor der Aktivierung.
4. Der Aktivierungs-Schalter schaltet das Tool ein/aus, ohne es zu löschen.

**Aufruf-Semantik.** Vom Modell erzeugte Argumente werden als JSON-Body (POST/PUT/PATCH) oder Query-String (GET) gesendet; der Antwort-Body ist das Tool-Ergebnis. API: CRUD + Test unter `/api/v1/tools/custom` (auditierte Operationen).

## Verfügbare Tools gruppiert nach MCP-Server

**Was es macht.** Unter der Verwaltung eigener Tools listet die Seite **Werkzeuge** **jedes dem Modell bereitgestellte Tool** für das aktuelle Profil, **gruppiert in eine Karte pro MCP-Server** (plus je eine *Built-in*- und *Custom*-Karte).

**So wird es benutzt.** Jede Karte zeigt den **MCP-Servernamen** als Titel, ein Badge mit der Tool-Anzahl und darunter die **Tool-Liste** (Name ohne das Präfix `mcp__<server>__`, plus Beschreibung). Praktisch, um auf einen Blick zu sehen, was jeder verbundene MCP-Server bietet. Die Schaltfläche **Aktualisieren** lädt die Liste neu.

## Sandboxter Code-Interpreter (`python_exec`)

**Was es macht.** Führt Python-Code in einem isolierten `python -I`-Subprozess aus, mit:

- rlimits für CPU, Speicher (`CODE_INTERPRETER_MEMORY_MB`), Dateigröße, fd-/Prozess-Anzahl;
- Wanduhr-Timeout (`CODE_INTERPRETER_TIMEOUT`, beendet die ganze Prozessgruppe);
- minimaler Umgebung und **kein Netzwerk** (Socket-Stubbing auf Python-Ebene);
- einem flüchtigen Arbeitsverzeichnis mit Datei-Ein-/Ausgabe: Eingabe-`files` werden vor dem Lauf materialisiert, erstellte Dateien im Ergebnis gemeldet (kleine Textdateien inline) und danach alles gelöscht.

**Konfiguration.** Standardmäßig aktiviert; Deaktivierung mit `CODE_INTERPRETER_ENABLED=false`.

**So wird es benutzt.** Mit aktiviertem Tool-Calling frage das Modell einfach nach etwas, das Berechnung/Code erfordert („führe dieses Skript aus", „analysiere diese Zahlen"); das Modell ruft `python_exec` selbstständig auf.
