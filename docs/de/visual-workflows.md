# Visuelle Knotengraph-Workflows (Phase 29)

SpiceSibyl hat zwei ergänzende Automatisierungs-Engines:

- **Agenten-Workflows** (`/workflows`, Phase 18) — Sie geben ein *Ziel* vor, und ein LLM
  iteriert autonom über das gesamte Tool-Register, bis eine Antwort entsteht. Mächtig, aber
  nicht deterministisch und ohne expliziten Kontrollfluss.
- **Visuelle Workflows** (`/graph-workflows`, Phase 29) — Sie zeichnen einen *Graphen*: ein
  **Trigger** speist **typisierte Knoten**, die durch Verbindungen verdrahtet sind. Die
  Engine führt den Graphen **deterministisch** aus, genau in der von Ihnen entworfenen Form.
  Die Agenten-Schleife bleibt hier als `llm.agent`-Knoten verfügbar, um Autonomie gezielt in
  eine deterministische Pipeline einzubauen.

![Editor für visuelle Workflows](screenshots/visual-workflow-editor.svg)

## Die Leinwand

Der Editor hat drei Bereiche:

- **Links** — Ihre Workflows und eine kategorisierte **Knoten-Palette** (Trigger · Aktionen ·
  Logik · Daten · KI). Jedes eingebaute, MCP- und benutzerdefinierte Tool erscheint automatisch
  als `tool.<name>`-Knoten — kein neuer Code pro Tool.
- Eine kleine Symbolleiste über der Leinwand bietet **Rückgängig/Wiederholen** (`Strg+Z` /
  `Strg+Umschalt+Z`, auch `Strg+Y`), **Knoten kopieren/einfügen** (`Strg+C` / `Strg+V` — fügt
  ein versetztes Duplikat mit gleichem Typ und gleichen Parametern ein) und **Kommentar**:
  ein rein clientseitiger Sticky-Note-Knoten ohne Ein-/Ausgänge, der nie in den Fluss
  eingebunden wird — die Engine verzeichnet ihn einfach als `skipped`, keine
  Backend-Änderung nötig. Tastenkombinationen werden beim Tippen in einem Feld ignoriert.
  Ein **Suchfeld** über der Palette filtert Knoten nach Bezeichnung oder Typ (und klappt
  passende MCP/benutzerdefinierte Gruppen während der Suche automatisch auf).
- **Mitte** — eine abhängigkeitsfreie **SVG-Leinwand**. Ziehen Sie Knoten zum Anordnen; ziehen
  Sie von einem **Ausgang** (rechts) zu einem **Eingang** (links), um zu verbinden. **Klicken
  Sie auf eine Kante**, um sie zu inspizieren: das rechte Panel zeigt Quelle → Ziel, die
  **Daten aus dem letzten Lauf** und eine Liste der **verfügbaren Felder mit fertigem
  Ausdruckspfad** (z. B. `$node.weather.output.result`) — ein Klick kopiert ihn als
  `{{ … }}`-Ausdruck; ein Button löscht die Verbindung. Schlägt ein Knoten fehl, erscheint
  seine **Fehlermeldung** rot unter dem Knoten im Live-Panel (und im Detail der
  Ausführungs-Ansicht).
- **Rechts** — der **Inspektor** des ausgewählten Knotens (seine Parameter, aus dem Schema des
  Knotentyps generiert) oder, wenn nichts ausgewählt ist, das **Ausführungs- & Trigger-Panel**.

Speichern mit **Speichern**, **Aktiv** umschalten, damit Trigger feuern, und **Jetzt ausführen**,
um den Graphen zu starten — Knoten leuchten in Echtzeit grün/blau/rot/grau (ok/läuft/Fehler/
übersprungen), während die Engine den Status per SSE streamt. Das Ausführungs-Panel hat ein
optionales **Payload**-Feld (JSON): sein Objekt wird zum `$trigger` des Laufs, sodass Graphen,
die `={{ $trigger.<Feld> }}` lesen, auch ohne Webhook-Aufruf manuell getestet werden können.

## Knotentypen

| Kategorie | Knoten |
|-----------|--------|
| **Trigger** | `manual`, `schedule`, `webhook`, `event` |
| **Aktion** | `tool.<name>` — jedes Register-Tool (RSS, read_url, Wetter, kb_search, http_request, python_exec, MCP, benutzerdefiniert…) · `http.request` (generischer HTTP-Aufruf) · `subworkflow` (führt einen anderen Workflow inline aus) |
| **Logik** | `if` (wahr/falsch-Zweig), `switch` (Fall-Zweige), `merge` (Eingänge sammeln), `wait` (wartet N Sekunden oder bis zu einem Zeitpunkt) |
| **Daten** | `set` (Objekt bauen), `filter` (passende Array-Elemente behalten), `code` (Python-Sandbox), `aggregate` (reduziert ein Array — sum/avg/min/max/count/concat über ein Feld), `batch` (teilt ein Array in Blöcke fester Größe) |
| **KI** | `llm.completion` (ein Provider-Aufruf), `llm.agent` (die volle Agenten-Schleife aus Phase 18) |

> **Failover-Ketten** — `llm.completion` und `llm.agent` bieten ein **Failover
> chain**-Menü, gespeist aus den benannten Modelllisten unter Einstellungen → Modelle →
> LLM-Failover-Ketten. Ist eine Kette gesetzt, versucht ein fehlgeschlagener Aufruf auf dem
> `model` des Knotens der Reihe nach die restlichen Modelle der Kette; die Knotenausgabe
> trägt dann `_failover: { tried: [...], used: "<model>" }`.

### HTTP-Aufrufe, Komposition und Fehlerbehandlung

- **`http.request`** — ruft eine beliebige externe HTTP-API auf (`method`, `url`,
  `query`/`headers`, `body`, `timeout` ≤ 120 s). Ausgabe: `{ status, ok, headers, json, text }`.
  Nicht-2xx-Antworten lösen standardmäßig einen Fehler aus (Retries und die
  *Bei-Fehler*-Richtlinie greifen); mit `allow_errors` kommt die Antwort unabhängig vom
  Status zurück.
- **`subworkflow`** — führt einen anderen Workflow desselben Profils als Kind-Lauf aus und
  liefert `{ run_id, workflow_id, status, output }` (`output` = Ausgabe des Endknotens des
  Kindes). Das `payload` wird zum `$trigger` des Kindes. Verschachtelung: max. 5 Ebenen.
- **Bei Fehler** (Inspektor, Abschnitt Erweitert) — nach erschöpften Retries: **Lauf
  abbrechen** (Standard), **auf dem Hauptzweig fortfahren** mit `{ error }`, oder **in den
  Fehlerzweig leiten**: der Knoten erhält einen dedizierten **`error`-Ausgang** und
  `{ error, input }` fließt über diesen Zweig, während `main` übersprungen wird — ein
  try/catch direkt auf der Zeichenfläche.
- **Benachrichtigungen** — `notify.telegram` (verknüpfter Telegram-Chat; optionaler
  `parse_mode` `Markdown`/`MarkdownV2`/`HTML` für echte Formatierung — CommonMark
  `**fett**` wird zu Telegrams eigenem Ein-Sternchen-`*fett*` normalisiert; Nachrichten
  über 4096 Zeichen werden automatisch in mehrere Nachrichten aufgeteilt), `notify.email`
  (SMTP über `SMTP_*`), `notify.webhook` (Slack/Discord/ntfy/…), `notify.inapp`
  (Web-UI-Glocke, keine Konfiguration nötig).
- **Ausführungs-Ansicht** — `/graph-workflows/runs`: das Register aller Läufe des Profils
  (Status, Trigger, Dauer, Ergebnisse pro Knoten, Live-SSE), getrennt vom Designer; der
  Editor hängt sich beim Öffnen eines Workflows wieder an dessen laufende Ausführung an.

## Ausdrücke

Jeder Parameter kann ein Literal **oder** ein Ausdruck sein, per Präfix unterschieden:

- `={{ … }}` — ein **sicherer Mini-Ausdruck**, geparst und über eine Whitelist ausgewertet
  (**kein `eval`/`exec`**). Sie können den Ausführungskontext navigieren und einen festen Satz
  reiner Funktionen aufrufen:

  ```
  ={{ $node.rss.output.result }}          # Ausgabe eines anderen Knotens
  ={{ $trigger.count }}                    # Trigger-Payload
  ={{ upper($json.title) }}                # Whitelist-Funktion
  ={{ default($trigger.name, 'Welt') }}
  ={{ $trigger.count > 3 }}                # Vergleiche → if/switch
  Hallo ={{ $trigger.name }}!              # String-Interpolation
  ```

  Kontext: `$node.<id>.output.<pfad>`, `$json` (primäre Eingabe des Knotens), `$trigger`,
  `$env` (WF_-präfixierte Umgebungsvariablen), `$now`. Funktionen: `default`, `upper`, `lower`,
  `trim`, `len`, `join`, `slice`, `first`, `last`, `get`, `keys`, `values`, `round`, …

- `=py: …` — eine **Ausweichluke** in die `python_exec`-Sandbox für echte Logik. `ctx`, `input`,
  `node`, `trigger` sind verfügbar; der letzte Ausdruck (oder eine `result`-Variable) wird zum Wert.

Alles, was nicht mit `=` beginnt, ist ein Literal.

## Trigger

Aus dem Ausführungs-Panel:

- **Schedule** — cron / RRULE / natürliche Sprache („jeden Tag um 9:00"), von derselben Engine
  wie Erinnerungen interpretiert. Eine Poll-Schleife feuert fällige Zeitpläne und berechnet die
  nächste Fälligkeit neu. (Feuert nur, wenn der Workflow **Aktiv** ist.)
- **Webhook** — eine öffentliche, tokengeschützte URL (`POST /api/v1/wf/hooks/{token}`). Der
  JSON-Body wird zu `$trigger`. Feuert nur, wenn der Workflow aktiv ist. Optional mit einem
  gemeinsamen Secret absichern: `POST /v1/graph-workflows/triggers/{tid}/rotate-secret`
  erzeugt eines (nur einmal angezeigt); danach muss die Anfrage den Header
  `X-Signature: sha256=<hex hmac-sha256 des Rohkörpers>` tragen, sonst wird sie mit 401
  abgelehnt, bevor der Body überhaupt geparst wird.
- **Event** — interne Ereignisse. `config.event` auf den Ereignisnamen setzen (leer oder
  `*` für alle). Heute sind zwei Ereignisse verdrahtet: `document.ingested` (nach dem
  Ingest eines KB-Dokuments/einer URL — Payload `{doc_id, filename, profile_id}`) und
  `chat.message.created` (nach dem Speichern eines Chat-Austauschs — Payload
  `{conversation_id, profile_id}`).

Sowohl **Schedule**- als auch **Event**-Trigger zählen aufeinanderfolgende Fehlschläge
(`fail_count`/`last_error`): nach `GRAPH_WORKFLOW_TRIGGER_MAX_FAILURES` (Standard 5)
Fehlschlägen in Folge deaktiviert sich der Trigger selbst und eine In-App-Benachrichtigung
wird ausgelöst. Erneutes Aktivieren (`POST /triggers/{tid}/enable`) setzt den Zähler zurück.

### Zeitpläne-Ansicht — Trigger-Übersicht über alle Workflows

`/graph-workflows/schedules` (Phase 30.e, gleiche Navbar-Gruppe und Feature-Flag) listet
**eine Zeile pro Trigger** über alle Workflows des Profils: Workflow-Name, Trigger-Typ,
nächste Ausführung (Schedule-Trigger), Status/Zeit des letzten Laufs, Zähler
aufeinanderfolgender Fehlschläge und ein Aktivieren/Deaktivieren-Schalter — alles auf einen
Blick, ohne jeden Workflow einzeln zu öffnen, plus **Ausführen** und **Löschen**. Backend:
`GET /v1/graph-workflows/schedules`.

> **Ein Trigger feuert nur, wenn sein *Workflow* aktiv ist** — das Aktivieren eines Triggers
> ist unabhängig vom Aktiv-Flag des Workflows (umschaltbar im Designer oder über die
> Aktiv/Inaktiv-Pille neben dem Workflow-Namen hier). Ein perfekt konfigurierter,
> aktivierter Trigger auf einem inaktiven Workflow feuert nie; das Formular
> **+ Neuer Trigger** warnt und bietet eine Ein-Klick-Aktivierung, wenn der gewählte
> Workflow inaktiv ist — das ist der häufigste Grund, warum ein frisch erstellter
> Zeitplan stillschweigend nichts tut.

**Trigger erstellen** (Phase 30.f) — das Panel **+ Neuer Trigger** wählt einen Workflow und
einen Typ (`schedule`/`webhook`/`event`); für `schedule` gibt es ein strukturiertes Muster
statt freier natürlicher Sprache: **Täglich** (eine Uhrzeit HH:MM), **Wöchentlich** (ein
oder mehrere Wochentage + Uhrzeit), **Cron** (Voreinstellungen wie "alle 15 Minuten"/
"stündlich"/"täglich um Mitternacht"/"wochentags um 9:00", die ein **freies 5-Felder-
Cron-Feld** befüllen, weiterhin bearbeitbar, mit `croniter` validiert), **Einmalig**
(optionales Datum + Uhrzeit). `event`-Trigger nehmen einen freien Ereignisnamen
(`document.ingested` und `chat.message.created` sind heute verdrahtet); `webhook` braucht
hier keine Zusatzkonfiguration — das Signatur-Secret wird nach dem Erstellen im Designer
generiert/erneuert.

### Produktion: Nebenläufigkeit, Token-Nutzung, Alarme

- **Nebenläufigkeitsgrenze** — ein `GRAPH_WORKFLOW_MAX_CONCURRENT_NODES`-Semaphore
  (Standard 8) begrenzt, wie viele unabhängige Knoten innerhalb eines Laufs parallel laufen.
- **Token-Nutzung** — die Ausgabe von `llm.completion`- und `llm.agent`-Knoten enthält
  einen `_usage`-Schlüssel (`{tokens_in, tokens_out, tokens_total}`, über die
  Agenten-Schritte summiert), wenn der Provider ihn meldet; sonst `null`. Kosten werden
  nicht geschätzt — es gibt noch keine Preistabelle pro Modell im Projekt.
- **Alarm bei wiederholten Fehlschlägen** — nach `GRAPH_WORKFLOW_RUN_FAILURE_ALERT_THRESHOLD`
  (Standard 3) aufeinanderfolgenden fehlgeschlagenen Läufen desselben Workflows wird einmalig
  (nicht bei jedem weiteren Fehlschlag) eine In-App-Benachrichtigung ausgelöst.
- **Antwort-Cache** — `llm.completion` und jeder `llm.agent`-Schritt nutzen denselben
  Antwort-Cache wie der Chat (`RESPONSE_CACHE_ENABLED`, `RESPONSE_CACHE_TTL_SECONDS`,
  `RESPONSE_CACHE_MAX_ENTRIES`, plus die Fuzzy-Schicht `SEMANTIC_CACHE_*` aus Phase 26). Eine
  identische Anfrage `(model, messages, temperature, max_tokens)` überspringt den Provider
  komplett; die Knotenausgabe trägt `_cache: "hit" | "semantic" | "miss"` neben `_usage`.
  Werkzeugaufrufende `llm.agent`-Schritte werden nie gecacht (gleiche Regel wie im Chat: eine
  Anfrage mit `tools` erhält nie einen Cache-Schlüssel).

## Versionen & Ausführungen

Jedes Speichern erzeugt eine unveränderliche Version; Sie können Versionen auflisten und
zurückrollen. Jede Ausführung speichert den ausgeführten Graphen, den aufgelösten Kontext und
einen Datensatz pro Knoten (Eingabe, Ausgabe, Fehler, Timing) zur nachträglichen Prüfung.

Da jeder Wert persistiert wird, braucht der Editor keinen Live-Lauf, um Daten zu zeigen:
beim Öffnen eines Workflows lädt er **die zuletzt aufgezeichnete Ausgabe jedes Knotens über
alle vergangenen Läufe** (`GET /{id}/node-outputs`) — ein Klick auf einen Pfeil zeigt also
die historisch durchgeflossenen Felder und Payloads, mit dem Hinweis „Daten aus einer
früheren Ausführung" samt Zeitstempel. Ein neuer Lauf ersetzt diese Werte durch Live-Daten.

**Export**: der *Exportieren*-Button (oder `GET /{id}/export`) lädt den Workflow als
portablen JSON-Snapshot herunter (`{ kind, schema_version, name, description, graph, … }`);
derselbe Body ist über `POST /v1/graph-workflows` wieder importierbar.

**Import**: der 📥-Button neben **Neu** (oben in der Workflow-Liste) öffnet eine
`.workflow.json`-Datei von der Festplatte — genau die Datei, die **Exportieren** erzeugt —
und legt daraus einen neuen Workflow an, der sofort zum Bearbeiten geöffnet wird. Gelesen
werden nur `name`, `description` und `graph`; reine Export-Felder (`kind`,
`schema_version`, `exported_at`, …) werden akzeptiert und ignoriert. Eine ungültige oder
keine Workflow-JSON-Datei wird clientseitig mit einer Fehlermeldung abgelehnt, statt an
den Server gesendet zu werden.

## API

Alles, was die UI tut, ist unter `/v1/graph-workflows` (JWT-geschützt) verfügbar. Siehe den
[Entwicklerleitfaden](../developer-guide.md) für die vollständige Endpoint-Referenz.

Einstellungen: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (standardmäßig an) aktiviert die Poll-Schleife;
`GRAPH_WORKFLOW_MAX_NODES` begrenzt die Graphgröße.
