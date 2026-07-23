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


![Visual editor — componentized canvas, palette and run panel](../screenshots/editor-overview.png)

<p align="center">
  <img src="../screenshots/run-panel-vars-secrets-versions.png" alt="Run panel: $vars editor, $secrets manager, version history" width="360" />
</p>

![Per-workflow shell — Editor | Runs | Schedules tabs with the run detail open](../screenshots/workflow-shell-runs.png)

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

### Die Einzel-Workflow-Ansicht — `/graph-workflows/{id}`

Jeder Workflow hat auch eine eigene Seite (öffnen über ⧉ in der Liste oder aus einer
Ausführungs-/Zeitplanzeile): eine Tab-Leiste **Editor | Ausführungen | Zeitpläne**,
beschränkt auf diesen Workflow. Der Ausführungen-Tab ist das vorgefilterte Register;
der Zeitpläne-Tab listet und erstellt Trigger nur für ihn. Die globalen Seiten bleiben
die workflow-übergreifenden Ansichten.

Der Editor selbst ist komponentisiert (Roadmap Phase 1): SVG-Canvas, Palette, Toolbar,
Knoten-/Kanten-Inspektor und Run-Panel sind eigenständige Angular-Komponenten unter
`features/workflows/editor/` — siehe `docs/frontend-overview.md`.

### Editor-DX — Testen, Pinnen, Navigieren (Phase 3)

Einen Graphen zu bauen und zu debuggen erfordert keine vollständigen Läufe:

- **Knoten testen** (⚡ im Inspector) führt **nur den ausgewählten Knoten** aus, mit den
  aktuellen — auch ungespeicherten — Parametern, und zeigt Output, aktiven Handle und
  Dauer inline (`POST /{id}/nodes/{node_id}/test`; nichts wird im Ausführungsregister
  aufgezeichnet). Der Input kommt vom gepinnten/letzten Output des Upstream-Knotens oder
  aus dem optionalen **Mock-Input**-JSON im Inspector.
- **Gepinnte Outputs** (📌) frieren den Output eines Knotens ein — ein Klick auf den
  letzten Output oder handeditiertes JSON. Knotentests, **Teilläufe** (*Ab diesem Knoten
  ausführen*) und Ausdrucks-Vorschauen lösen `$node.<id>.output` aus dem Pin statt aus
  der Historie auf: ideal, um stromabwärts eines echten Webhook-Payloads zu entwickeln,
  ohne ihn erneut auszulösen. Pins werden mit dem Workflow gespeichert (und wandern mit
  dem Export), zeigen ein 📌-Badge auf der Leinwand und werden von **Produktionsläufen
  komplett ignoriert** (manual/schedule/webhook/event).
- **Letzte Ausführung** im Inspector zeigt Status, Output und Fehler des ausgewählten
  Knotens (Live-Lauf, Test oder Historie), ohne die Leinwand zu verlassen.
- **Mehrfachauswahl**: Shift+Klick fügt Knoten hinzu/entfernt sie; Ziehen bewegt die
  ganze Auswahl; `Strg+A` wählt alles; `Strg+C/V` kopiert & fügt die Auswahl **inklusive
  ihrer internen Kanten** ein (IDs neu vergeben); `Entf`/`Backspace` löscht sie.
- **Pan & Zoom**: leere Leinwand ziehen für Pan, Mausrad zoomt um den Cursor. Eine
  **Minimap** (unten rechts) zeigt den ganzen Graphen plus Viewport — Klick/Ziehen
  navigiert, Doppelklick passt ein. Die Toolbar ergänzt **Anordnen** (schichtweises
  Auto-Layout, rückgängig machbar) und **⛶ Einpassen**.
- Die **Template-Galerie** (✨) öffnet sich als **großes zentriertes Modal** über dem
  Editor: ein mehrspaltiges Karten-Raster mit größerer Graph-Vorschau, Kategorie,
  Fluss-Kette (Knotennamen mit →), Knoten-/Verbindungszahl und vollständiger
  Beschreibung — vor dem Import nach Kategorie filterbar. Die **Workflow-Liste ist
  einklappbar** (▾/▸ in der Kopfzeile, sitzungsübergreifend gemerkt), sodass die
  Knoten-Palette den Platz der Seitenleiste bekommt.

## Knotentypen

| Kategorie | Knoten |
|-----------|--------|
| **Trigger** | `manual`, `schedule`, `webhook`, `event`, `error`, `success` (anderer Workflow abgeschlossen — Phase 6.1), `file.watch` / `email.inbound` (Poll-basierte Trigger — Phase 6.2) |
| **Aktion** | `tool.<name>` — jedes Register-Tool (RSS, read_url, Wetter, kb_search, http_request, python_exec, MCP, benutzerdefiniert…) · `http.request` (generischer HTTP-Aufruf) · `subworkflow` (führt einen anderen Workflow inline aus) · `human.approval` (pausiert, bis ein Mensch genehmigt/ablehnt — Phase 4.4) · `human.input` (pausiert, bis ein Mensch ein JSON-Schema-Formular ausfüllt — Phase 10.1) · `wait.event` (pausiert, bis ein korreliertes externes Ereignis eintrifft — Phase 10.2) |
| **Logik** | `if` (wahr/falsch-Zweig), `switch` (Fall-Zweige), `merge` (Eingänge sammeln), `wait` (wartet N Sekunden oder bis zu einem Zeitpunkt) |
| **Daten** | `set` (Objekt bauen), `filter` (passende Array-Elemente behalten), `code` (Python-Sandbox), `aggregate` (reduziert ein Array — sum/avg/min/max/count/concat über ein Feld), `batch` (teilt ein Array in Blöcke fester Größe), `db.query` (parametrisiertes SQL — sqlite/postgres), `file.read` / `file.write` (Workspace-Speicher), `file.parse` (JSON/CSV/Zeilen unterwegs parsen) |
| **KI** | `llm.completion` (ein Provider-Aufruf), `llm.agent` (die volle Agenten-Schleife aus Phase 18), `llm.classify` / `llm.extract` (garantiert strukturierte Ausgabe — Phase 4.1) |

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
- **Timeout (ms)** (Inspektor, Abschnitt Erweitert) — harte Zeitobergrenze für einen
  *einzelnen* Ausführungsversuch (`0` deaktiviert es, max. 600 000). Ein abgelaufener
  Versuch wird abgebrochen und schlägt wie jeder andere Fehler fehl, unterliegt also weiter
  den Retries und der *Bei Fehler*-Politik — der idiomatische Schutz für einen hängenden
  `http.request`, `llm.agent` oder MCP-Tool, das sonst den ganzen Lauf blockieren würde.
- **Retries & Backoff-Strategie** (Inspektor, Abschnitt Erweitert — Phase 2.1) — führt den
  Knoten bis zu N-mal erneut aus und wartet `backoff` Sekunden zwischen den Versuchen.
  **Fest** wartet immer `backoff` Sekunden; **Exponentiell** wartet `backoff × 2^Versuch`
  (max. 60 s pro Pause). Neue `http.request`- und `llm.*`-Knoten kommen mit sinnvollen
  Voreinstellungen aus dem Katalog (z. B. HTTP: 2 Retries, 2 s exponentiell, 60 s Timeout).
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

### Neue Knoten der Phase 4 — strukturierte KI, DB/Dateien, menschliche Freigabe

- **`llm.classify` / `llm.extract`** (Phase 4.1) — KI-Knoten mit **garantierter
  Ausgabeform**: `llm.classify` ordnet den Input einer der deklarierten `categories` zu
  (Ausgabe `{ category, confidence }` — eine Kategorie außerhalb der Liste löst einen
  Fehler aus, Retries greifen); `llm.extract` extrahiert Daten gemäß einem **JSON Schema**
  aus dem Inspektor (`required`-Felder werden erzwungen, Ausgabe `{ data }`). Beide nutzen
  Modell-Picker, Failover-Kette und Antwort-Cache wie `llm.completion`.
- **`db.query`, `file.read`, `file.write`, `file.parse`** (Phase 4.2) — parametrisiertes
  SQL (`{ rows, count, rowcount }`, max. 1000 Zeilen; sqlite-Datenbanken liegen im
  Workspace-Speicher, Postgres per `dsn` aus `$secrets`) und Dateiknoten auf dem
  **Workspace-Speicher** (`GRAPH_WORKFLOW_FILES_DIR`, max. 10 MB): `json → {data}`,
  `csv → {rows, count}`, `lines → {lines, count}`, `text → {text, size}`. Jeder Pfad wird
  *innerhalb* des Speichers aufgelöst — absolute Pfade und `..`-Traversal schlagen fehl.
- **`human.approval`** (Phase 4.4) — der Lauf **pausiert** (Status `waiting`), erzeugt
  eine Freigabe-Anfrage, benachrichtigt in-app (optional Telegram) und wartet auf die
  Entscheidung aus der Ausführungs-Ansicht (**✓ Genehmigen / ✕ Ablehnen**, optionaler
  Kommentar) oder per API (`GET /approvals`, `POST /approvals/{id}/decision`). Die
  Entscheidung leitet den Graphen über den **`approved`**- bzw. **`rejected`**-Ausgang;
  `timeout` (Standard 24 h, Obergrenze `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` = 7 Tage) und
  `onTimeout` (`reject` | `fail`) regeln den Ablauf. Das Warten überlebt Neustarts
  (Checkpoints der Phase 2.4); das Abbrechen eines wartenden Laufs schließt die Anfrage
  als `cancelled`.

### Erweiterte menschliche Interaktion — `human.input`, `wait.event` (Phase 10)

Zwei weitere Knoten pausieren den Lauf (`waiting`) genauso wie `human.approval` — sie
verallgemeinern dessen Anfrage-Datensatz zu einer `kind` (`approval` | `input` | `event`),
sodass alle drei denselben Poll/Resume-Kreislauf teilen und einen Backend-Neustart auf
identische Weise überstehen.

**`human.input`** — die Anfrage trägt ein **per JSON Schema definiertes Formular**
(`schema`-Parameter: Felder, Typen, `required`, `enum`). Entschieden wird über die
Ausführungs-Ansicht (die Felder rendern als Formular) oder per API; die übermittelten
`data` werden **gegen das Schema validiert**, bevor sie akzeptiert werden. Der Lauf
setzt über den **`submitted`**-Zweig mit `{ data, status, comment, decided_by }` als
Ausgabe fort; ein Timeout folgt `onTimeout` (`branch` leitet über den **`timeout`**-Zweig,
`fail` lässt den Knoten scheitern). Ermöglicht Abläufe wie „fehlenden Wert vom Bediener
erfragen" — z. B. Betrag und Kategorie einer Ausgabe, bevor es weitergeht.

```
POST /v1/graph-workflows/approvals/{aid}/submit  { data: {...}, comment? }
```

**`wait.event`** — der Lauf pausiert, bis ein **externes System** ein Ereignis mit
passender **Korrelations-ID** zustellt. `correlationId` (Ausdruck, z. B. eine Bestell-ID
aus `$trigger`) benennt den Schlüssel; `POST /v1/graph-workflows/events/{correlation_id}`
(authentifiziert, profilgebunden) weckt den Lauf und liefert dessen `payload` als
**Ausgabe** des Knotens über den **`main`**-Zweig. Gleiches `timeout` / `onTimeout`
(`branch` | `fail`) wie bei `human.input`. Deckt echte asynchrone Callbacks ab —
Zahlungen, digitale Signaturen, Tickets, Third-Party-Webhooks — ganz ohne Polling. Ein
`waiting`-Lauf belegt keinen `max_concurrent_runs`-Slot.

```
POST /v1/graph-workflows/events/{correlation_id}  { payload: {...} }
```

Parameter (beide Knoten): `title`, `message` (Ausdruck), `timeout` (Sekunden, Standard
24 h, Obergrenze `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT`), `onTimeout`. `human.input`
nimmt zusätzlich `schema` (das JSON Schema des Formulars); `wait.event` nimmt stattdessen
`correlationId`.

### Phase 5 — Metriken, Import/Sharing, generierte Workflows

- **Metriken** (Phase 5.1) — `GET /v1/graph-workflows/stats` aggregiert pro Workflow:
  Laufzahlen nach Ausgang, **Erfolgsquote**, **Ø Dauer** und die **LLM-Token-Summen**
  aus dem `_usage`-Schlüssel der `llm.*`-Knoten. Die Ausführungs-Ansicht zeigt sie als
  Dashboard-Leiste; das Lauf-Detail zeigt die Token des geöffneten Laufs.
- **Export/Import & Sharing** (Phase 5.2) — der Export trägt jetzt ein `secrets`-Array
  (nur die **Namen** der referenzierten `$secrets`); `POST /v1/graph-workflows/import`
  validiert den Snapshot (Schema + Knotenlimit) und meldet nicht-blockierende Warnungen
  (unbekannte Knotentypen, fehlende `$secrets`). Workflows lassen sich in einen
  Workspace teilen (`POST /v1/workspaces/{ws}/workflows`) und von Mitgliedern als Kopie
  ins eigene Profil importieren (`POST /{ws}/workflows/{wid}/import`).
- **Generierte Workflows** (Phase 5.3) — der 🪄-Knopf öffnet den Dialog „Beschreibe, was
  du willst“ mit **Modell-Picker** und optionaler **Failover-Kette**:
  `POST /v1/graph-workflows/generate` erzeugt aus dem Knotenkatalog einen **validierten,
  normalisierten Entwurf** (unbekannte Typen/kaputte Kanten entfernt, fehlender Trigger
  ergänzt, Auto-Layout) und öffnet ihn im Editor. Die UI nutzt das Streaming-Pendant
  `POST /generate/stream`: `log`-SSE-Events zeigen jeden Schritt als **Live-Protokoll**
  (Katalog, Modellaufruf, Antwort, Validierung, Layout) statt eines bloßen Spinners.

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
  `$env` (WF_-präfixierte Umgebungsvariablen), `$vars` (Workflow-Variablen), `$secrets` (Profil-Secrets, nur für die Dauer eines Laufs entschlüsselt), `$now`. Funktionen: `default`, `upper`, `lower`,
  `trim`, `len`, `join`, `slice`, `first`, `last`, `get`, `keys`, `values`, `round`, …

- `=py: …` — eine **Ausweichluke** in die `python_exec`-Sandbox für echte Logik. `ctx`, `input`,
  `node`, `trigger` sind verfügbar; der letzte Ausdruck (oder eine `result`-Variable) wird zum Wert.

Alles, was nicht mit `=` beginnt, ist ein Literal.

## Variablen & Secrets — `$vars` / `$secrets`

Zwei Konfigurationsebenen halten Werte aus den Knoten-Parametern heraus (Roadmap Phase 1):

- **Variablen (`$vars`)** — Schlüssel/Wert-Paare pro Workflow, editierbar im Abschnitt
  *Variablen* des Run-Panels, lesbar in jedem Knoten als `{{ $vars.name }}`. Ein Wert,
  der als JSON parst, behält seinen nativen Typ. Variablen wandern mit Export/Import und
  über die API (`variables` bei `POST`/`PATCH`); Änderungen erhöhen die Graph-Version
  **nicht**.
- **Secrets (`$secrets`)** — profilweite Zugangsdaten für alle deine Workflows
  (API-Tokens, Verbindungsstrings…), verwaltet im Abschnitt *Secrets* des Run-Panels.
  Werte werden **mit Fernet verschlüsselt gespeichert** (abgeleitet aus
  `VAULT_SECRET_KEY`) und **nie von der API zurückgegeben** — die Liste zeigt nur Namen.
  Referenz: `{{ $secrets.NAME }}` (z. B. in einem `http.request`-Header). Die Engine
  entschlüsselt sie nur für die Dauer eines Laufs; der persistierte Kontext enthält sie
  nie, *Test expression* liefert `***`, und der Export lässt sie bewusst weg.

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
- **Error** (Phase 2.5) — feuert, wenn der Lauf eines *anderen* Workflows fehlschlägt.
  `config.workflow_id` beschränkt ihn auf einen beobachteten Workflow (leer / `*` = alle).
  Payload: `{workflow_id, workflow_name, run_id, error, failed_node}`; auf der
  Zeichenfläche dient der Trigger-*Knoten* `error` als Einstiegspunkt. Schleifensicher:
  ein Workflow reagiert nie auf eigene Fehlschläge, und von Error-Triggern gestartete
  Läufe lösen keine weiteren Error-Trigger aus. Ideal für zentrales Alerting mit den
  `notify.*`-Knoten.

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
- **Lauf-Warteschlange pro Workflow** (Phase 2.3) — **Max. gleichzeitige Läufe** im
  Abschnitt **Ausführung** des Run-Panels (oder `max_concurrent_runs` per API, `0` =
  unbegrenzt): Läufe über dem Limit entstehen im Status **`queued`** (Trigger-Payload wird
  am Lauf geparkt) und starten FIFO, sobald ein Slot frei wird. Warteschlangen-Läufe
  erscheinen in der Läufe-Ansicht und lassen sich abbrechen; `subworkflow`-Kindläufe
  umgehen die Warteschlange (ein wartendes Kind würde den Eltern-Lauf blockieren).
- **Checkpoint & Wiederaufnahme** (Phase 2.4) — der Laufkontext (Ausgabe **und aktive
  Ausgangs-Handles** jedes Knotens) wird nach jeder Welle persistiert. Beim Start (Flag
  `GRAPH_WORKFLOW_RESUME_ON_STARTUP`, Standard true) werden durch Crash/Neustart
  hängengebliebene `running`/`pending`-Läufe vom Checkpoint fortgesetzt: fertige Knoten
  laufen nicht erneut, nur der fehlende Teilgraph wird ausgeführt; verwaiste Knotenläufe
  werden als Fehler („interrupted by restart") geschlossen.
- **Error-Trigger** (Phase 2.5) — siehe Abschnitt Trigger: ein Workflow mit `error`-Trigger
  startet, wenn ein anderer fehlschlägt, mit
  `{workflow_id, workflow_name, run_id, error, failed_node}` als `$trigger`.
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

Das Run-Panel hat einen Abschnitt **Versionen**: jeder Snapshot mit Zeitstempel und
Ein-Klick-**Wiederherstellen** — die Wiederherstellung sichert zuerst den aktuellen
Graphen als neue Version, ein Rollback ist also immer umkehrbar.

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
derselbe Body ist über `POST /v1/graph-workflows` wieder importierbar. Seit Phase 7.2
enthält der Snapshot auch `environments` — die benannten Umgebungen des Workflows (nur
`$vars`-Overlays und `$secrets`-**Aliase**, nie Werte; eine fixierte `version` gilt in
der Zielumgebung erst wieder nach erneuter Beförderung dort, da Versionsnummern nicht
zwischen Workflows portabel sind).

**Import**: der 📥-Button neben **Neu** (oben in der Workflow-Liste) öffnet eine
`.workflow.json`-Datei von der Festplatte — genau die Datei, die **Exportieren** erzeugt —
und legt daraus einen neuen Workflow an, der sofort zum Bearbeiten geöffnet wird. Gelesen
werden nur `name`, `description` und `graph`; reine Export-Felder (`kind`,
`schema_version`, `exported_at`, …) werden akzeptiert und ignoriert. Eine ungültige oder
keine Workflow-JSON-Datei wird clientseitig mit einer Fehlermeldung abgelehnt, statt an
den Server gesendet zu werden.

**Lauf wiederholen (Replay)**: jeder beendete Lauf (abgeschlossen, fehlgeschlagen oder
abgebrochen) zeigt im Detailbereich der Läufe-Ansicht einen **↻ Wiederholen**-Button. Er
startet den Workflow mit der *Trigger-Payload* dieses Laufs erneut gegen den **aktuellen**
Graphen — nach dem Beheben eines Knotens reproduzierst du so die ursprüngliche Eingabe mit
einem Klick und bestätigst den Fix (API: `POST /v1/graph-workflows/runs/{rid}/replay`).
Teilläufe können nicht wiederholt werden und liefern `409`.

## Phase 6 — Engine-Erweiterung (Trigger, Schleifen, Komposition)

Implementiert in v3.1.0 (Phase 38):

- **`success`-Trigger (6.1)** — das Spiegelbild des `error`-Triggers: feuert, wenn ein
  Lauf eines anderen Workflows **erfolgreich abschließt** (`config.workflow_id`-Filter,
  dieselben Anti-Loop-Guards). Payload: `{workflow_id, workflow_name, run_id, output}` —
  „A, dann B"-Pipelines ohne Subworkflows.
- **Mehrere Cron-Ausdrücke pro Zeitplan (6.1)** — das `cron`-Muster akzeptiert eine
  `crons`-Liste (in der UI ein Ausdruck pro Zeile): der nächste Lauf ist der früheste über
  alle Ausdrücke — gemischte Zeitpläne auf einem Trigger.
- **`file.watch`-Trigger (6.2)** — Poll-basiert (nutzt die Zeitplan-Schleife, kein
  inotify): überwacht einen Unterordner des Workspace-Speichers (`config.path`) mit einem
  Glob-Muster; feuert pro erstellter/geänderter Datei mit `$trigger = {path, event, size}`.
  Der erste Poll initialisiert nur den Zustand; `config.interval` hat als Untergrenze
  `GRAPH_WORKFLOW_WATCH_POLL_SECONDS` (60 s).
- **`email.inbound`-Trigger (6.2)** — fragt ein IMAP-Postfach nach ungelesenen
  Nachrichten ab (Zugangsdaten über `$secrets`, `password_secret` benennt das Secret) mit
  Absender-/Betrefffiltern. `$trigger = {from, subject, body, attachments}`; Anhänge
  landen unter `email_attachments/` im Speicher, lesbar mit `file.read`.
- **`while`-Knoten (6.3)** — bedingungsgesteuerte Schleife (Polling asynchroner APIs,
  Pagination) ohne Subworkflow-Rekursion. Die `condition` wird **vor jeder Iteration neu
  ausgewertet**, mit `$item` = Ausgabe des Schleifenkörpers der vorherigen Iteration und
  `$index` = Iterationsnummer. Pflicht-Obergrenze: `maxIterations` (Standard 100), hartes
  Limit `GRAPH_WORKFLOW_WHILE_MAX_ITERATIONS` (1000). Ausgabe auf `done`:
  `{items, count, capped}`.
- **Subworkflow-Verträge (6.4)** — `input_schema` / `output_schema` (JSON Schema,
  Abschnitt **Verträge** im Run-Panel; wandern mit Export/Import): der
  `subworkflow`-Knoten validiert die Eingabe vor dem Kind-Lauf und die Ausgabe bei der
  Rückgabe. Workflows mit Eingabevertrag erscheinen in der Palette als typisierte
  **`workflow.<id>`**-Knoten, und der LLM-Generator (Phase 5.3) kann sie komponieren.
- **`kb.search`-Knoten (6.5)** — semantische Suche über die Wissensbasis aus einem
  Workflow heraus: `query`, `top_k`, optionaler `document_ids`-Filter. Ausgabe:
  `{results: [{text, score, source, chunk_index}], count}` — RAG in Workflows ohne
  generischen `llm.agent`.
- **Ratenbegrenzung pro Host (6.6)** — `http.request` (und `notify.webhook`) wird pro
  Host über ein gleitendes Ein-Minuten-Fenster gedrosselt: `maxRequestsPerMinute` am
  Knoten und/oder die globale Karte `GRAPH_WORKFLOW_RATE_LIMITS` (`host=rpm` oder JSON;
  die strengere Grenze gewinnt). Anfragen über der Grenze **warten, sie schlagen nicht
  fehl**; die Wartezeit erscheint als `rate_limited_s` in der Knotenausgabe.

## Betrieb und Governance (Phase 7)

**Neustart ab dem fehlgeschlagenen Knoten** (7.1): fehlgeschlagene Läufe zeigen einen
**↺ Erneut versuchen**-Button. Anders als Replay — das mit dem Original-Trigger auf dem
aktuellen Graphen von vorn beginnt — erzeugt Retry einen neuen Lauf über den **exakten
Graph-Snapshot des Ursprungslaufs**, gesät mit den bereits im Checkpoint gespeicherten
Ausgaben: nur der fehlgeschlagene Knoten und sein nachgelagerter Teilgraph laufen erneut
(`POST /runs/{rid}/retry`, `409` wenn der Lauf nicht `failed` ist). Retry und Replay
speichern `origin_run_id`, sichtbar im Lauf-Detail.

**Umgebungen** (7.2): der Abschnitt **Umgebungen** im Ausführungspanel definiert benannte
Umgebungen als JSON-Map — `{"prod": {"vars": {...}, "secrets": {"TOKEN": "TOKEN_PROD"},
"version": 5}}`. `vars` überlagern die `$vars` des Workflows, `secrets` mappen
`$secrets.<alias>` auf ein anderes gespeichertes Secret um (nur Namen, nie Werte),
`version` fixiert die in dieser Umgebung ausgeführte Graphversion. **⇧ Befördern**
(`POST /{id}/environments/{env}/promote`) fixiert die aktuelle Version — "promote to
prod", während der Editor am aktuellen Graphen weiterarbeitet. Die Umgebung wird bei
manuellen Läufen (`environment`) und in der Konfiguration von Schedule-/Webhook-Triggern
gewählt; jeder Lauf speichert seine Umgebung (Badge in der Läufe-Ansicht).

**Audit und Freigaberollen** (7.3): `GET /{id}/audit` liefert das Audit-Protokoll des
Workflows (Erstellen, Ändern, Aktivieren, Ausführen, Genehmigen, Beförderungen…),
neueste zuerst. Das Teilen in einen Workspace trägt jetzt eine **Rolle**: `viewer`
(einsehen/importieren), `editor` (darf auch Läufe starten — ausgeführt unter dem Profil
des Eigentümers), `approver` (darf auch `human.approval`-Anfragen entscheiden).

**Metriken pro Knoten** (7.4): `GET /{id}/stats/nodes` aggregiert die Historie pro Knoten
— Ausführungen nach Ausgang, Fehlerrate, Durchschnitts-/p50-/p95-Dauer, LLM-Tokens —
sortiert nach dem problematischsten Knoten. Der neue **Zustand**-Tab der Shell zeigt die
Tabelle und das Audit-Protokoll.

**Genehmigung per Telegram** (7.5): `human.approval`-Benachrichtigungen mit aktiviertem
Telegram tragen Inline-Buttons **✅ Genehmigen / ❌ Ablehnen**; der Bot prüft die
Verknüpfung Chat ↔ Profil und entscheidet die Anfrage wie der Web-Endpoint (der erste
Schreiber gewinnt), und der wartende Lauf setzt in Sekunden fort.

### Erweiterter Editor — Diff, Notizen, Schritt-Debugging (Phase 8)

**Versions-Diff (8.1)** — im Bereich **Versionen** des Run-Panels vergleicht die
*Vergleichen*-Zeile zwei gespeicherte Versionen (**Diff**): hinzugefügte Knoten leuchten
grün, geänderte gelb, entfernte werden in der Diff-Leiste aufgelistet. Die Position eines
Knotens wird bewusst ignoriert. API: `GET /{id}/versions/{a}/diff/{b}`.

**Notizen und Rahmen (8.2)** — die Buttons **📝 Notiz** und **▢ Rahmen** platzieren
Haftnotizen und Gruppierungsrahmen auf der Leinwand (ziehbar, Doppelklick zum Bearbeiten,
leer = löschen). Sie werden mit dem Graphen gespeichert, versioniert und exportiert, aber
die **Engine ignoriert sie vollständig**.

**Schritt-Debugging (8.3)** — **🐞 Debug** aktiviert den Debug-Modus; auf den Punkt eines
Knotens klicken setzt einen **Haltepunkt**. **Debug starten** erstellt den Lauf im Status
**`paused`**; dann **⏭ Schritt** (nächster Knoten, dann pausieren), **▶ Fortfahren** (bis
zum nächsten Haltepunkt) und **⏹ Stopp**. API: `POST /{id}/run` mit `debug:true`, dann
`POST /runs/{id}/debug` (`{command, breakpoints?, input?}`). Ein optionales `input` mockt
die Eingabe des nächsten Knotens. Sitzungen, die länger als
`GRAPH_WORKFLOW_DEBUG_MAX_PAUSE` (Standard 1 h) pausiert bleiben, werden abgebrochen.

### Workflows als Ökosystem-Werkzeuge (fase 9)

Ein Workflow kann zu einem **Baustein** werden, den andere aufrufen.

- **Als Werkzeug veröffentlichen (9.1)** — gib dem Workflow einen **Eingabevertrag**
  (Run-Panel → *Verträge*), aktiviere **Als Werkzeug veröffentlichen** und **schalte ihn
  aktiv**: er wird zu einem Werkzeug `workflow__<id>`, das von **`llm.agent`**-Knoten, von
  **`tool.*`**-Knoten anderer Workflows und vom **Chat** aufgerufen werden kann. Der Aufruf
  führt ihn als normalen Lauf aus (Metriken und Audit gelten) und liefert seine Ausgabe
  zurück. Eine Tiefenbegrenzung (`GRAPH_WORKFLOW_TOOL_MAX_DEPTH`, Standard 3) verhindert
  endlose Rekursion. `GET /tools` listet die veröffentlichten Werkzeuge.
- **MCP-Server des Produkts (9.2)** — dieselben Workflows sind für externe MCP-Clients
  (Claude Desktop, IDEs) über `POST /v1/graph-workflows/mcp` erreichbar, ein JSON-RPC-2.0-
  Endpunkt (`initialize` / `tools/list` / `tools/call` / `ping`); ein `tools/call` führt
  den Workflow inline aus (Ursprung `mcp`).
- **`chat`-Trigger (9.3)** — füge einen **`chat`**-Trigger hinzu und schließe den Graphen
  mit einem **`chat.reply`**-Knoten ab: `POST /v1/graph-workflows/{id}/chat` mit
  `{ message, session_id? }` führt den Workflow mit `$trigger = {session_id, message,
  history}` aus und liefert die Antwort. Der Sitzungszustand bleibt über Turns erhalten
  (Bereinigung nach `GRAPH_WORKFLOW_CHAT_SESSION_TTL`).
- **OpenAPI-Import (9.4)** — `POST /v1/graph-workflows/openapi/import` (`spec` inline oder
  `url`) macht aus jeder Operation einen vorkonfigurierten **`http.request`**-Knoten
  (Methode, URL, Query, Auth auf `$secrets` abgebildet), ungespeichert zum Ablegen auf der
  Leinwand zurückgegeben.

### Tests, Trockenlauf und Kostenschätzung (fase 11)

Behandle den Workflow wie Code, im Run-Panel → **Tests & Trockenlauf**:

- **Testsuiten (11.1)** — speichere einen **Testfall**: fester `$trigger`-Payload +
  **Prüfungen** auf die Ausgabe eines Knotens (`equals`, `contains`, `json_path`,
  `schema`). **Tests ausführen** lässt jeden Fall als echten, beobachtbaren Lauf laufen
  und zeigt grün/rot je Prüfung. Ein Knoten mit externem Effekt (`http.request`,
  `db.query`, `notification.*`/`email.*`, `llm.*`) mit **fixierter Ausgabe** (fase 3.2)
  macht den Test deterministisch — kein echter Aufruf; ohne Pin läuft der Knoten weiterhin
  wirklich.
- **Vollständiger Trockenlauf (11.2)** — **Trockenlauf starten** simuliert den gesamten
  Graphen: jeder Knoten mit externem Effekt wird simuliert (sein Pin, oder ein typisierter
  Platzhalter) — **nichts Externes geschieht je**. Der Bericht zeigt den Ausführungspfad,
  die simulierten Ausgaben und welche Knoten einen echten Effekt gehabt hätten. Vor der
  Aktivierung eines Zeitplans auf einem neuen Graphen zu verwenden.
- **Kostenschätzung (11.3)** — statische Tokens/Monat-Projektion: `llm.*`-Knoten des
  Graphen × historischer Durchschnitt Tokens/Lauf × Frequenz des aktiven Zeitplans. Nur
  Tokens, keine erfundene Preisliste.

### Budgets, Aufbewahrung und Schwärzung (fase 12)

Schutzmaßnahmen, bevor eine Zeitplan-+-LLM-Kombination in Produktion geht — neben Audit
Trail und Freigabe-Rollen (fase 7.3).

- **Budgets und Kontingente (12.1)** — setze eine monatliche **Token**- und/oder
  **Lauf**-Obergrenze auf einem Workflow (Run-Panel → **Budget & Kontingente**, unter
  Tests & Trockenlauf) und/oder eine profilweite Grenze
  (`GET/PUT /v1/graph-workflows/budget`), die zusätzlich über alle Workflows gilt. Die
  Nutzung wird über den aktuellen UTC-Kalendermonat aus derselben Lauf-Historie berechnet,
  die bereits die fase-5.1-Statistiken nutzen — kein Zähler, den man manuell zurücksetzen
  müsste, der Zeitraum erneuert sich von selbst. Ist eine Grenze erreicht, stoppen neue
  Läufe: ein manueller Lauf wird mit einem expliziten Fehler abgelehnt, und ein
  Zeitplan-/Event-Trigger, der bei erschöpftem Budget weiter auslöst, deaktiviert sich
  nach der üblichen Serie aufeinanderfolgender Fehlschläge selbst (derselbe Mechanismus,
  der bereits einen defekten Trigger stilllegt). Das Überschreiten von 80 % einer Grenze
  (konfigurierbar über `GRAPH_WORKFLOW_BUDGET_WARN_PCT`) löst eine einmalige
  In-App-Benachrichtigung pro Zeitraum aus.
- **Aufbewahrung und Schwärzung (12.2)** — gib einem Workflow ein eigenes
  Aufbewahrungsfenster für Läufe in Tagen, oder belasse den instanzweiten Standard
  (`GRAPH_WORKFLOW_RUNS_RETENTION_DAYS`, 0 = für immer aufbewahren); eine periodische
  Bereinigung löscht abgeschlossene Läufe (completed/failed/cancelled) jenseits der
  Schwelle — ein noch laufender oder auf einen Menschen wartender Lauf wird nie
  angerührt. Trägt die Ausgabe eines Knotens etwas Sensibles, liste dessen gepunktete
  Pfade (z. B. `body.card_number`) im **Schwärzen**-Feld des Inspektors: diese Felder
  werden überall, wo die Ausgabe persistiert, live gestreamt oder exportiert wird, als
  `***` maskiert — der reale Wert bleibt jedoch das, was der *nächste* Knoten sieht, ein
  geschwärztes Feld kann also während des Laufs selbst weiterhin die nachgelagerte Logik
  steuern.

### Copilot und Workflow-as-Code (fase 13)

- **Ausdrucks-Autovervollständigung (13.1)** — tippe `$node.` in ein Ausdrucksfeld und der
  Inspektor schlägt die Ids der Knoten vor, die dem gerade bearbeiteten vorgelagert sind;
  nach der Wahl einer Id vervollständigt `.` mit den echten Ausgabefeldern dieses Knotens
  (aus einer angehefteten Ausgabe oder seinem letzten Lauf). `$vars.` und `$secrets.`
  vervollständigen ebenso gegen die deklarierten Variablen und die Secret-*Namen* des
  Workflows — nie deren Werte —, und `$item`/`$index` erscheinen für einen Knoten
  innerhalb eines for/repeat-Rumpfs.
- **Erklären / reparieren (13.2)** — schlägt ein Lauf fehl, zeigt der fehlgeschlagene
  Knoten im Run-Panel eine Schaltfläche **Erklären / reparieren**: sie sendet Typ,
  aktuelle Parameter, empfangene Eingabe und Fehler des Knotens an das LLM, das mit einer
  kurzen, allgemeinverständlichen Ursache antwortet und, wenn es einer konkreten Korrektur
  sicher ist, ein korrigiertes Parameterobjekt als Diff zeigt. Nichts wird automatisch
  übernommen — **Korrektur übernehmen** fügt sie dem Knoten auf der Leinwand hinzu (danach
  ganz normal speichern), **Verwerfen** verwirft sie.
- **Git-Synchronisierung von Definitionen (13.3)** — verknüpfe einen Workflow mit einem
  Git-Repo (Run-Panel → Versionen → **Git-Synchronisierung**: Repo-URL, Branch, Name eines
  Secrets mit dem Zugriffstoken, optionaler Pfad im Repo) und jede ab dann gespeicherte
  Version wird dort als JSON committet — ein Commit pro Version, Nachricht mit Version und
  Autor. **Jetzt pullen** holt den Branch und importiert eine dort geänderte Datei
  (z. B. nach einem gemergten PR) als neue **Entwurfsversion** — der Live-Graph wird nie
  überschrieben, du prüfst/stellst sie wieder her wie jede andere Version.

### Verteilte Ausführung und Skalierbarkeit (fase 14)

**Remote-Runner (fase 14.1).** Manche Arbeit muss anderswo als im Backend-Prozess
stattfinden: eine interne API, die nur aus dem Netzwerk des Kunden erreichbar ist, eine
nicht öffentlich exponierte Datenbank, ein schwerer `code`-Knoten, der eine größere
Maschine braucht, lokale Inferenz auf einer GPU. Unter **Graph workflows → Runner**
registrierst du einen Runner (Name, Labels wie `gpu`/`internal-network`/`dmz` und optional
eine Positivliste erlaubter Knotentypen) — du erhältst ein einmaliges Token, nur einmal
angezeigt. Starte den Agentenprozess überall dort, wo ausgehender Zugriff zum Backend
möglich ist:

```
SIBYL_RUNNER_TOKEN=<token> python -m app.runner.agent
```

Er sendet Heartbeats und fragt per Long-Poll nach Arbeit; nichts erfordert einen
eingehenden Port. Gib einem Knoten ein **runOn**-Label (erweiterte Einstellungen), das zu
einem Label deines Runners passt, und er läuft dort statt im Backend — nur für
Knotentypen, die keinen Backend-Kontext benötigen (`http.request`, `code`, `db.query`,
`set`, `if`, `switch`, `merge`, `filter`, `aggregate`, `batch`, `wait`, `queue.publish`);
alles, was in seinen Parametern `$secrets` referenziert, kommt beim Runner bereits als
aufgelöster Literalwert an, nie der Vault. Kein passender Runner innerhalb des Timeouts
online: **runOnFallback** `fail` (Standard) lässt den Knoten wie jeden anderen Fehler
fehlschlagen (Retry/On error gelten weiterhin), `local` führt ihn stattdessen im Backend aus.

**Sandbox des `code`-Knotens (fase 14.2).** Nichts zu aktivieren — der `code`-Knoten lief
schon immer in einem isolierten Subprozess (CPU-/Speicher-/Zeitlimits, kein Netzwerk),
im Backend genauso wie auf einem Remote-Runner.

**Skalierung des Engines (fase 14.3).** Im Hintergrund wird jeder Lauf an die
Prozessinstanz "verpachtet", die ihn ausführt, und der Pachtvertrag erneuert sich selbst,
solange der Lauf aktiv ist; ein durch einen Absturz zurückgelassener Pachtvertrag steht
der nächsten (auch neu gestarteten) Instanz frei — derselbe Checkpoint/Resume-Mechanismus
wie in Phase 2.4. Bei einer Einzelinstanz-Bereitstellung ist nichts zu konfigurieren; es
ist die Nahtstelle, über die eine künftige Multi-Replika-/Postgres-Bereitstellung
koordinieren würde.

**Message-Queue-Trigger (fase 14.4).** Ein **Queue publish**-Knoten sendet eine Nachricht
an ein benanntes Topic; ein **Queue consume**-Trigger auf einem anderen (oder demselben)
Workflow feuert einmal pro empfangener Nachricht, mit `$trigger = {message, topic,
headers}`. Standardmäßig werden Nachrichten persistiert (`GRAPH_WORKFLOW_QUEUE_DRIVER=db`),
sodass bei einem Neustart nichts verloren geht; kein externer Broker erforderlich. Ein
echter Broker (RabbitMQ/Kafka/MQTT) kann später als direkter Ersatz eingebunden werden,
ohne Knoten oder Trigger anzufassen.

**CLI (fase 14.5).** `python -m app.cli.sibyl_wf` steuert dieselbe REST-API von einem
Terminal oder einer CI-Pipeline aus — `run <id>`, `export`/`import`, `test <id>
<node_id>`, `logs <run_id>` — authentifiziert mit einem Bearer-Token (`SIBYL_API_KEY`).

### Konnektoren und multimodale Knoten (fase 15)

**Kuratierte Konnektoren (fase 15.1).** Eine Palettenkategorie **Konnektoren** liefert
handgefertigte `connector.<service>.<operation>`-Knoten über `http.request`, mit bereits
verdrahtetem Endpunkt, Auth und Payload: **Slack** / **Discord** (Nachricht posten),
**GitHub** / **GitLab** (Issue anlegen), **Jira** (Issue anlegen), **Google Sheets**
(anhängen / lesen). Zugangsdaten stammen aus `$secrets` (z. B. Token-Feld auf
`={{ $secrets.SLACK_TOKEN }}`), nie fest verdrahtet. Da darunter `http.request` läuft,
gelten Retry/Backoff, Knotentest, Pins und Host-Ratenlimits; die Ausgabe ist die
HTTP-Ausgabe plus `{operation}`.

**`ssh.exec` (fase 15.2).** Führt einen Befehl auf einem entfernten Host per SSH aus —
Schlüssel oder Passwort aus `$secrets`, Host-Allowlist über
`GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS` (leer = beliebig), Timeout pro Befehl. Ausgabe
`{stdout, stderr, exit_code}`; ein Exit ungleich null löst aus (Retry / Bei Fehler
greifen), außer **Exit ungleich null erlauben** ist gesetzt.

**`browser` (fase 15.3).** Headless-Browser-Scraping/-Checks mit Playwright: URL öffnen,
optional auf einen CSS-Selektor warten, dann **Text**, ein **Attribut** oder einen
**Screenshot** (im Workspace-Speicher abgelegt, von `file.*` lesbar) extrahieren. Läuft in
einem Worker-Thread mit Timeout pro Aktion; benötigt `playwright` (+ Browser) im Image.

**`rss.read`-Trigger (fase 15.4).** Pollt einen RSS/Atom-Feed und feuert **einen Lauf pro
neuem Eintrag**, dedupliziert per guid, mit `$trigger = {title, link, published, summary,
guid}`. Nutzt die file.watch/Queue-Poll-Schleife; der erste Poll seedet nur die
Gesehen-Menge (`GRAPH_WORKFLOW_RSS_MAX_ENTRIES` begrenzt die Feuer pro Poll). Anhängen mit
`{url, interval}`. Ideal für „News → LLM → Benachrichtigung".

**`doc.convert` (fase 15.5).** Konvertiert ein PDF/DOCX/HTML/PPTX/…-Dokument aus dem
Workspace-Speicher via markitdown nach **Markdown**, Ausgabe `{markdown, chars, path}`;
`path` fällt auf den Knoteneingang zurück, verkettet also direkt mit `file.watch`
`$trigger.path`. Die übrigen Medienknoten (`audio.transcribe`, `image.ocr`,
`image.generate`, `tts`) hängen von Provider-Unterstützung ab und sind zurückgestellt.

### Zustand und Ausführungssemantik (fase 16)

**Persistenter Zustand über Läufe hinweg (fase 16.1).** Drei Knoten der Kategorie **Data**
lesen und schreiben einen Key/Value-Speicher pro Workflow, der **über Läufe hinweg erhalten
bleibt**: `state.get` → `{key, value, found}` (mit optionalem `default`, wenn der Schlüssel
fehlt/abgelaufen ist), `state.set` (dessen `value` standardmäßig die Knoteneingabe ist) und
`state.increment` (atomare numerische Addition, gibt den neuen Wert zurück — ideal für Zähler
und Rate-Fenster). Ein `ttlSeconds` gibt einem Schlüssel ein Ablaufdatum; ein abgelaufener
Schlüssel liest sich als nicht vorhanden. Der Speicher ist im Run-Panel einsehbar und editierbar
— `GET/PUT/DELETE /v1/graph-workflows/{id}/state` — manuelle Änderungen werden im Audit-Log
erfasst, und er ist **niemals Teil eines Exports** (eigene Tabelle, nicht die Workflow-Definition).

**Trigger-Idempotenz (fase 16.2).** Setze einen `dedupKey`-Ausdruck auf einen **Webhook**- oder
**Event**-Trigger (z. B. `{{ $trigger.order_id }}`): Derselbe Schlüssel, der innerhalb von
`dedupWindowSeconds` zweimal geliefert wird, gibt die **ursprüngliche** `run_id` zurück (HTTP 200,
`deduped: true`), statt einen zweiten Lauf zu starten — Exactly-once-Verarbeitung für Systeme,
die Zustellungen wiederholen. Schlüssel werden mit TTL gespeichert; das Standardfenster stammt
aus `GRAPH_WORKFLOW_DEDUP_DEFAULT_WINDOW_SECONDS`.

**Kompensationen / Saga (fase 16.3).** Verdrahte eine `compensate`-Kante aus einem Knoten mit
Seiteneffekt zu einem kleinen Rollback-Teilgraphen. Schlägt der Lauf **weiter unten fehl**,
durchläuft die Engine die abgeschlossenen Knoten in **umgekehrter Reihenfolge** und führt den
Kompensationszweig jedes Knotens aus, gespeist mit dessen eigener Ausgabe (z. B. den reservierten
Bestand freigeben, wenn die spätere Zahlung fehlschlägt). Kompensations-Knotenläufe sind im
Live-Stream mit `compensation: true` markiert; ein Fehler in einer Kompensation markiert den
Lauf als `failed` mit zusammengesetztem Fehler. Vollständig opt-in — ein Graph ohne
`compensate`-Kante bleibt unberührt.

**Lauf-Priorität (fase 16.4).** Eine `priority` auf einem Lauf (aus der Trigger-Konfiguration
`priority` oder der Start-API `priority`) lässt die Warteschlange pro Workflow höher priorisierte
Läufe zuerst befördern, FIFO innerhalb derselben Priorität — ein interaktiver Lauf kann sich vor
einen Batch-Backfill drängen.

## Detaillierte Beispiele je Funktion

Vollständige, reproduzierbare Rezepte — eines pro Engine-Bereich. Jedes Beispiel nennt das
**Ziel**, die **Graph-Kette**, die **Knoten-für-Knoten-Konfiguration** mit konkreten Werten
und Ausdrücken, die **erwartete Ausgabe** und die **demonstrierte Funktion**. Sie sind zum
Nachbauen von Hand auf der Leinwand oder zum Anpassen gedacht: ersetze die
URLs/Städte/APIs durch deine eigenen. Viele haben ein per Klick importierbares Gegenstück in
der ✨-Galerie (siehe [Beispielgraphen](../examples/graph-workflows.md)).

> **Konvention** — wo `={{ … }}` steht, ist es ein Ausdruck (ausgewertet); ein nackter Wert
> ist ein Literal. Knoten-IDs (`rss`, `api`, `triage`…) sind die im Inspector gewählten und
> in `$node.<id>.output`-Pfaden verwendeten.

### 1. Morgendlicher RSS-Digest — Schedule-Trigger + Tool + LLM

**Ziel:** jeden Morgen um 08:00 die Titelseite eines Feeds in fünf Stichpunkte zusammenfassen
und ein betiteltes Digest-Objekt bauen.

**Graph:** `schedule → tool.fetch_rss → llm.completion → set`

**Knoten:**
- `schedule` (`schedule`-Trigger) — Muster **Täglich**, Uhrzeit `08:00`. Merke: feuert nur
  bei **aktivem** Workflow.
- `rss` (`tool.fetch_rss`) — `url`: `={{ $vars.FEED }}` (setze `FEED =
  https://hnrss.org/frontpage` im *Variablen*-Panel).
- `summary` (`llm.completion`) — Modell aus dem Auswähler; `prompt`:
  ```
  Fasse diese Nachrichten in 5 knappe Stichpunkte zusammen:
  ={{ $node.rss.output.result }}
  ```
- `digest` (`set`) — baut das Objekt:
  - `title` → `Digest vom ={{ $now }}`
  - `body` → `={{ $node.summary.output.content }}`

**Erwartete Ausgabe:** `{ title: "Digest vom 2026-07-20…", body: "• …\n• …" }`.

**Demonstriert:** Schedule-Trigger, Ausgabe→Eingabe-Verkettung via `$node.<id>.output`,
`$vars`, String-Interpolation, die Kette Trigger → Aktion → KI → Daten.

### 2. Webhook → Antwort aus der Wissensdatenbank (RAG) — `$trigger` + HMAC-Signatur

**Ziel:** eine öffentliche URL bereitstellen, die eine Frage **ausschließlich** aus den
abgerufenen KB-Passagen beantwortet.

**Graph:** `webhook → kb.search → llm.completion → set`

**Knoten:**
- `webhook` (`webhook`-Trigger) — nach dem Speichern das Signatur-Secret mit **Secret
  rotieren** erzeugen (nur einmal angezeigt).
- `search` (`kb.search`) — `query`: `={{ $trigger.question }}`, `top_k`: `5`.
- `answer` (`llm.completion`) — `prompt`:
  ```
  Antworte NUR mit diesen Passagen. Reichen sie nicht, sag es.
  Frage: ={{ $trigger.question }}
  Passagen: ={{ $node.search.output.results }}
  ```
- `out` (`set`) — `answer` → `={{ $node.answer.output.content }}`.

**Aufruf** (Workflow aktiv):
```bash
BODY='{"question":"wie konfiguriere ich SMTP?"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')
curl -X POST https://dein-host/api/v1/wf/hooks/$TOKEN \
     -H "X-Signature: sha256=$SIG" -H 'Content-Type: application/json' -d "$BODY"
```

**Demonstriert:** Webhook-Trigger, Lesen von `$trigger.<feld>`, RAG mit `kb.search`,
HMAC-Schutz (eine Anfrage ohne gültigen Header wird mit 401 abgewiesen, bevor sie überhaupt
interpretiert wird).

### 3. Bedingter Zweig — `if` + Whitelist-Ausdrücke

**Ziel:** eine Webseite prüfen und danach verzweigen, ob ein Schlüsselwort auftaucht.

**Graph:** `schedule → tool.read_url → if → set (wahr) | set (falsch)`

**Knoten:**
- `fetch` (`tool.read_url`) — `url`: `={{ $vars.PAGE }}`.
- `check` (`if`) — `condition`:
  `={{ 'sale' in lower($node.fetch.output.result) }}`.
- `hit` (`set`, **true**-Zweig) — `alert` → `„sale“ gefunden um ={{ $now }}`.
- `miss` (`set`, **false**-Zweig) — `status` → `keine Änderung`.

**Erwartete Ausgabe:** nur ein Zweig läuft; der Knoten des nicht gewählten Zweigs wird als
`skipped` protokolliert.

**Demonstriert:** `if`-Routing, `in`-Operator, `lower()`-Funktion, sich gegenseitig
ausschließende Zweige.

### 4. API-Aufruf mit Retries und Fehlerzweig — Try/Catch auf der Leinwand

**Ziel:** eine externe API aufrufen, zweimal wiederholen und nur dann **alarmieren**, wenn
jeder Versuch scheitert.

**Graph:** `manual → http.request → set (main) | notify.telegram (error)`

**Knoten:**
- `api` (`http.request`) — `method` `GET`, `url` `={{ $vars.API_URL }}`, `timeout` `60`.
  Abschnitt **Erweitert**: **Versuche** `2`, **Backoff** `2` s **Exponentiell**, **Bei
  Fehler → Zum Fehlerzweig routen**.
- `ok` (`set`, Ausgang **main**) — `status` → `={{ $node.api.output.status }}`,
  `data` → `={{ $node.api.output.json }}`.
- `alert` (`notify.telegram`, Ausgang **error**) — `text`:
  `API nicht erreichbar: ={{ $node.api.output.error }}`.

**Erwartete Ausgabe:** bei Erfolg führt `main` `{ status, ok, headers, json, text }`; sind
die Versuche erschöpft, fließt `{ error, input }` über den `error`-Handle und der
`main`-Zweig wird übersprungen. Knoten `api` bleibt als **Fehler** protokolliert, auch wenn
er den Fehlerzweig routet.

**Demonstriert:** `http.request`, Retries mit exponentiellem Backoff, die Richtlinie *Bei
Fehler → Fehlerzweig*, `$vars`.

### 5. Mehrzweig-Routing — `switch`

**Ziel:** je Kanal an eine von drei Warteschlangen routen.

**Graph:** `manual → switch → set | set | set`

**Knoten:**
- `route` (`switch`) — `value`: `={{ default($trigger.channel, 'a') }}`; `cases`:
  `["a","b","c"]`. Ausgangs-Handles: `a`, `b`, `c`, `default`.
- drei `set`-Knoten an ihren Handles.

**Ausprobieren:** trage `{"channel":"b"}` in **Ausführungs-Payload** ein → nur Zweig `b`
läuft; ein Wert außerhalb der Liste landet auf `default`.

**Demonstriert:** Mehrfach-`switch`, `default()`, manueller Ausführungs-Payload als
`$trigger`.

### 6. For-each-Schleife über ein Array — `loop`/`done`-Handles, `$item`/`$index`

**Ziel:** für jede URL einer Liste diese laden und die Titel sammeln.

**Graph:** `manual → set (Liste) → for → (loop) tool.read_url → set` · `(done) set`

**Knoten:**
- `urls` (`set`) — `list` → `={{ ['https://a.dev','https://b.dev'] }}` (ein alleinstehender
  Ausdruck bleibt eine native Liste).
- `loop` (`for`) — `items`: `={{ $node.urls.output.list }}`.
- Körper, am **`loop`**-Handle:
  - `get` (`tool.read_url`) — `url`: `={{ $item }}` (im Körper `$item`/`$index` verwenden,
    **nicht** `$node.loop.output`).
  - `title` (`set`) — `t` → `={{ slice($node.get.output.result, 0, 80) }}`.
- Fortsetzung, am **`done`**-Handle:
  - `all` (`set`) — `titles` → `={{ $node.loop.output.items }}`.

**Erwartete Ausgabe:** auf `done` liefert `loop` `{ items: [...], count: 2 }`.

**Demonstriert:** `for`, Scope je Iteration (`$item`/`$index`), Trennung Körper (`loop`) /
Fortsetzung (`done`), Ergebnissammlung.

### 7. Bedingungsgesteuerte Schleife — `while` (Paginierung / Polling)

**Ziel:** Seiten laden, solange die API einen Cursor liefert.

**Graph:** `manual → while → (loop) http.request → set` · `(done) aggregate`

**Knoten:**
- `pager` (`while`) — `condition`:
  `={{ $index == 0 or $item.next != null }}`, `maxIterations`: `50`.
- Körper (`loop`):
  - `page` (`http.request`) — `url`:
    `={{ $vars.API }}?cursor=={{ default($item.next, '') }}`.
  - `norm` (`set`) — `items` → `={{ $node.page.output.json.items }}`,
    `next` → `={{ $node.page.output.json.next }}` (wird das `$item` der nächsten Iteration).
- `flat` (`aggregate`, auf `done`) — `op` `concat` über das Feld `items`.

**Erwartete Ausgabe:** auf `done`, `{ items, count, capped }` (`capped: true`, wenn das Limit
erreicht wird).

**Demonstriert:** `while` (Bedingung vor jedem Durchlauf neu ausgewertet mit `$item` =
vorherige Körperausgabe), `maxIterations`-Limit, `aggregate`.

### 8. Datenpipeline — `set` + `filter` + `aggregate` mit dem `=py:`-Notausgang

**Ziel:** nur große Bestellungen behalten und ihre Summen addieren.

**Graph:** `manual → set → filter → aggregate → set`

**Knoten:**
- `orders` (`set`) — `list` →
  `={{ [{'id':1,'total':40},{'id':2,'total':150},{'id':3,'total':300}] }}`.
- `big` (`filter`) — `items`: `={{ $node.orders.output.list }}`; **keep**-Maske über den
  Sandbox-Notausgang: `=py:[o['total'] > 100 for o in input]`.
- `sum` (`aggregate`) — `op` `sum` über das Feld `total`.
- `out` (`set`) — `total` → `={{ $node.sum.output.result }}` (`450`).

**Demonstriert:** `filter` mit Boolean-Maske, den `=py:`-Notausgang (echte Comprehension),
`aggregate` (`sum/avg/min/max/count/concat`).

### 9. Komposition mit Vertrag — `subworkflow` + `input_schema`/`output_schema`

**Ziel:** einen „Kunde anreichern“-Workflow als Schritt eines anderen wiederverwenden und
Ein-/Ausgabe validieren.

**Voraussetzung** — im Kind-Workflow, Run-Panel → **Verträge**:
- `input_schema`: `{"type":"object","required":["email"],"properties":{"email":{"type":"string"}}}`
- `output_schema`: `{"type":"object","required":["score"]}`

**Graph (Eltern):** `manual → subworkflow → set`

**Knoten:**
- `enrich` (`subworkflow`) — **Workflow**: das Kind aus dem Menü wählen; `payload`:
  `={{ {'email': $trigger.email} }}`. Der Payload wird **vor** dem Kind-Run gegen
  `input_schema` validiert; die zurückkommende Ausgabe gegen `output_schema`.
- `out` (`set`) — `score` → `={{ $node.enrich.output.output.score }}`.

**Erwartete Ausgabe:** `{ run_id, workflow_id, status, output }` — `output` ist die Ausgabe
des Terminalknotens des Kindes. Verschachtelung max. 5 Ebenen; Selbstrekursion lässt den Run
scheitern.

**Demonstriert:** `subworkflow`, JSON-Schema-E/A-Verträge, beobachtbarer Kind-Run
(`trigger_type: subworkflow`). Mit einem `input_schema` erscheint das Kind zudem als
typisierter **`workflow.<id>`**-Knoten in der Palette.

### 10. Menschliches Freigabe-Gate — `human.approval`

**Ziel:** ein Deployment anhalten, bis eine Person freigibt.

**Graph:** `manual → human.approval → notify.inapp (approved) | notify.inapp (rejected)`

**Knoten:**
- `gate` (`human.approval`) — `title`: `Deploy ={{ $trigger.subject }}`, `message`:
  `Freigabe bestätigen?`, `timeout`: `86400` (24 h), `onTimeout`: `reject`,
  `telegram`: `true` (Inline-Buttons im Chat).
- `go` (`notify.inapp`, **approved**-Handle) — `title`: `Deploy freigegeben`.
- `stop` (`notify.inapp`, **rejected**-Handle) — `title`: `Deploy abgelehnt`.

**Entscheiden:** der Run geht in den Zustand **`waiting`** (violetter Chip). Öffne ihn unter
**Ausführungen** → **✓ Freigeben / ✕ Ablehnen** (mit Kommentar), oder per API:
```
POST /v1/graph-workflows/approvals/{aid}/decision  {"approved": true, "comment": "ok"}
```

**Erwartete Ausgabe:** `{ approved, status, comment, decided_by }` auf dem gewählten Zweig.
Das Warten übersteht Neustarts (Checkpoints) und belegt **keinen** Nebenläufigkeits-Slot.

**Demonstriert:** HITL, `waiting`-Zustand, `approved`/`rejected`-Handles, Entscheidung im Web
oder per Telegram.

### 10a. Freigabeformular für Spesen — `human.input`

**Ziel:** einen validierten Betrag + eine Kategorie erfassen, bevor es weitergeht.

**Graph:** `manual → human.input → notify.inapp (submitted) | notify.inapp (timeout)`

**Knoten:**
- `form` (`human.input`) — `title`: `Spesenfreigabe`, `schema`: `{ "type": "object",
  "required": ["amount", "category"], "properties": { "amount": {"type": "number"},
  "category": {"type": "string", "enum": ["travel", "meals", "software", "other"]} } }`,
  `timeout`: `86400`, `onTimeout`: `branch`.
- `logged` (`notify.inapp`, **submitted**-Handle) — Text nutzt
  `={{ $node.form.output.data.category }}: ={{ $node.form.output.data.amount }}`.
- `expired` (`notify.inapp`, **timeout**-Handle).

**Ausfüllen:** der Run geht in den Zustand **`waiting`**; öffne ihn unter **Ausführungen** —
die Felder rendern aus dem Schema — oder per API:
```
POST /v1/graph-workflows/approvals/{aid}/submit  {"data": {"amount": 42, "category": "travel"}}
```

**Erwartete Ausgabe:** `{ data, status, comment, decided_by }` auf `submitted` — `data` wird
serverseitig **gegen `schema` validiert**, bevor es akzeptiert wird.

**Demonstriert:** HITL-Formularerfassung, JSON-Schema-Validierung, `submitted`/`timeout`-
Handles.

### 10b. Auf Zahlung warten — `wait.event`

**Ziel:** einen Checkout-Lauf anhalten, bis ein externer Zahlungsanbieter sie bestätigt.

**Graph:** `manual → wait.event → notify.inapp (main) | notify.inapp (timeout)`

**Knoten:**
- `wait` (`wait.event`) — `correlationId`: `={{ $trigger.order_id }}`, `timeout`: `3600`,
  `onTimeout`: `branch`.
- `paid` (`notify.inapp`, **main**-Handle) — Text: `={{ $node.wait.output }}`.
- `expired` (`notify.inapp`, **timeout**-Handle).

**Zustellen:** ein externes System (oder ein manueller Test) sendet ein POST an die
Korrelations-ID:
```
POST /v1/graph-workflows/events/ord-123  {"payload": {"paid": true}}
```

**Erwartete Ausgabe:** das zugestellte `payload` wird auf `main` zur Ausgabe des Knotens.

**Demonstriert:** Ereigniszustellung per Korrelations-ID, echte asynchrone Callbacks ohne
Polling.

### 11. Ticket-Triage — `llm.classify` + `switch` + `file.write` CSV

**Ziel:** ein Ticket mit garantierter Struktur labeln, routen und protokollieren.

**Graph:** `manual → llm.classify → switch → notify.inapp ×3` (+ `file.write`)

**Knoten:**
- `triage` (`llm.classify`) — `input`: `={{ $trigger.text }}`; `categories`:
  `billing, bug, question`. Eine Antwort außerhalb der Liste wirft einen Fehler (also greifen
  Retries).
- `route` (`switch`) — `value`: `={{ $node.triage.output.category }}`; `cases`:
  `["billing","bug","question"]`.
- drei `notify.inapp` an ihren Handles.
- `log` (`file.write`) — `path`: `tickets/triage-log.csv`, `format`: `csv`, `append`: `true`,
  `content`: `={{ {'cat': $node.triage.output.category, 'text': $trigger.text} }}`.

**Ausprobieren:** Payload `{"text":"meine Rechnung ist falsch"}` → Kategorie `billing`.

**Demonstriert:** `llm.classify` (garantierte `{category, confidence}`-Ausgabe), `switch` auf
das Ergebnis, `file.write` CSV im Append-Modus im Workspace-Speicher.

### 12. Strukturierte Extraktion — `llm.extract` mit einem JSON Schema

**Ziel:** typisierte Felder aus Freitext extrahieren.

**Graph:** `manual → llm.extract → db.query`

**Knoten:**
- `parse` (`llm.extract`) — `input`: `={{ $trigger.text }}`; `schema`:
  ```json
  {
    "type": "object",
    "required": ["name", "amount"],
    "properties": {
      "name":   {"type": "string"},
      "amount": {"type": "number"},
      "due":    {"type": "string"}
    }
  }
  ```
- `save` (`db.query`) — `driver`: `sqlite`, `database`: `invoices.db`,
  `query`: `INSERT INTO invoices(name, amount, due) VALUES (?,?,?)`,
  `params`: `={{ [$node.parse.output.data.name, $node.parse.output.data.amount, $node.parse.output.data.due] }}`.

**Erwartete Ausgabe:** `parse` → `{ data: {...}, model, _usage }` (die `required`-Felder der
obersten Ebene werden geprüft; eine nicht konforme Antwort wirft einen Fehler). `save` →
`{ rows, count, rowcount }`.

**Demonstriert:** `llm.extract` mit JSON Schema, parametrisiertes `db.query`
(`?`-Platzhalter für sqlite; die Datei liegt im Workspace-Speicher).

### 13. Postgres-Abfrage mit sicheren Zugangsdaten — `db.query` + `$secrets`

**Ziel:** Zeilen aus Postgres lesen, ohne je das DSN in den Graphen zu schreiben.

**Voraussetzung:** Run-Panel → **Secrets** → `PG_DSN` hinzufügen (verschlüsselt gespeichert,
nie exportiert).

**Graph:** `schedule → db.query → notify.email`

**Knoten:**
- `q` (`db.query`) — `driver`: `postgres`, `dsn`: `={{ $secrets.PG_DSN }}`,
  `query`: `SELECT id, email FROM users WHERE created_at > $1`,
  `params`: `={{ [$vars.SINCE] }}` (`$1…`-Platzhalter für postgres).
- `mail` (`notify.email`) — `to`: `={{ $vars.OPS }}`, `subject`: `Neue Nutzer`,
  `body`: `={{ $node.q.output.count }} neue: ={{ $node.q.output.rows }}`.

**Demonstriert:** `db.query` postgres, verschlüsselte Secrets (`$secrets`, nur während des
Runs aufgelöst, `***` in *Ausdruck testen*), parametrisierte Platzhalter.

### 14. Broadcast an alle Kanäle — `notify.*` parallel

**Ziel:** eine Nachricht an In-App, Telegram, E-Mail und Webhook zustellen, mit sanftem
Ausfall nicht konfigurierter Kanäle.

**Graph:** `manual → set → notify.inapp + notify.telegram + notify.email + notify.webhook`

**Knoten:**
- `msg` (`set`) — `text` → `={{ $trigger.text }}`.
- die vier `notify.*` parallel an `msg`. Bei Telegram/E-Mail/Webhook **Bei Fehler → Auf dem
  Hauptzweig fortfahren** setzen, so lässt ein nicht konfigurierter Kanal (keine verknüpfte
  Chat, kein SMTP) den Run nicht scheitern; die In-App-Glocke funktioniert immer.
- `notify.telegram` mit `parse_mode`: `Markdown`, falls `text` aus einem `llm.*`-Knoten in
  CommonMark kommt (`**fett**` wird zu Telegrams `*fett*` normalisiert).

**Demonstriert:** paralleles Fan-out, die vier Benachrichtigungskanäle, die
*Fortfahren*-Richtlinie für Fehlertoleranz.

### 15. Zentraler Alarm-Hub — `error`-Trigger

**Ziel:** ein Wächter-Workflow, der alarmiert, wenn **irgendein anderer** Workflow scheitert.

**Graph:** `error → set → notify.telegram`

**Knoten:**
- `error`-Trigger — Run-Panel → **＋ error**; lasse `config.workflow_id` **leer**, um auf
  *jeden* Fehler zu reagieren (oder setze eine, um einen einzelnen Workflow zu beobachten).
  Aktiviere den Workflow.
- `fmt` (`set`) — `text` →
  `❌ ={{ $trigger.workflow_name }} Knoten ={{ $trigger.failed_node }}: ={{ $trigger.error }}`.
- `send` (`notify.telegram`) — `text`: `={{ $node.fmt.output.text }}`.

**Erwartete Ausgabe:** bei jedem andernorts gescheiterten Run startet dieser mit
`$trigger = {workflow_id, workflow_name, run_id, error, failed_node}`.

**Demonstriert:** `error`-Trigger, Schleifenschutz (reagiert nie auf eigene Fehler,
fehlerausgelöste Runs kaskadieren nicht). Spiegelbild: der `success`-Trigger für „A dann
B“-Pipelines.

### 16. Autonomer Agent in einer Pipeline — `llm.agent`

**Ziel:** ein offenes Ziel an die Agentenschleife übergeben (mit integrierten + MCP +
Custom-Tools) und ihre Antwort ausliefern.

**Graph:** `manual → llm.agent → notify.inapp`

**Knoten:**
- `agent` (`llm.agent`) — Modell aus dem Auswähler; optionale **Failover chain**; `goal`:
  `={{ default($trigger.goal, 'Recherchiere Neues zu X und fasse es zusammen') }}`;
  `max_steps`: `8`.
- `bell` (`notify.inapp`) — `body`: `={{ $node.agent.output.content }}`.

**Erwartete Ausgabe:** `{ content, _usage, _cache }`; `_usage` summiert Tokens über alle
Agentenschritte. Ein erfolgreicher Failover ist bleibend (spätere Schritte starten vom
funktionierenden Modell).

**Demonstriert:** Autonomie dort eingefügt, wo nötig, Zugriff auf das gesamte Tool-Register
in einem deterministischen Graphen, `_usage`/Failover.

### 17. dev/prod-Umgebungen ohne Graph-Duplikat — `environments` + Promote

**Ziel:** derselbe Graph mit unterschiedlichen Endpoints und Zugangsdaten zwischen prod und
dev.

**Setup** — Run-Panel → **Umgebungen**:
```json
{
  "prod": { "vars": {"API": "https://api.example.com"},
            "secrets": {"TOKEN": "TOKEN_PROD"}, "version": 5 },
  "dev":  { "vars": {"API": "https://staging.example.com"},
            "secrets": {"TOKEN": "TOKEN_DEV"} }
}
```
Ein Knoten liest `={{ $vars.API }}` und `={{ $secrets.TOKEN }}`: das Umgebungs-Overlay
überschreibt `$vars` und mappt die `$secrets`-Aliase um (nur Namen, nie Werte).

**Promoten:** **⇧ Promoten** (`POST /{id}/environments/prod/promote`) pinnt die aktuelle
Version auf `prod`, während du am Graphen weiterarbeitest. Wähle die Umgebung bei einem
manuellen Run (Feld `environment`) oder in der Trigger-Config; jeder Run protokolliert sein
Badge.

**Demonstriert:** benannte Umgebungen, `$vars`-Overlay / `$secrets`-Aliasing, Versions-Pin,
„promote to prod“.

### 18. Schritt-Debugging mit Breakpoints — Debug-Modus (Phase 8.3)

**Ziel:** die aufgelöste Eingabe Knoten für Knoten prüfen, bevor sie ausgeführt wird.

**Schritte:**
1. **🐞 Debug** schaltet den Modus ein; klicke den Punkt eines Knotens, um einen
   **Breakpoint** zu setzen.
2. **Debug starten** — der Run entsteht **`paused`**, vor jedem Knoten (`POST /{id}/run` mit
   `debug:true`).
3. **⏭ Schritt** führt den nächsten Knoten aus und pausiert erneut; **▶ Fortfahren** geht zum
   nächsten Breakpoint; **⏹ Stopp** bricht ab (`POST /runs/{id}/debug` mit
   `{command, breakpoints?, input?}`).
4. Der wartende Knoten ist violett und die Debug-Leiste zeigt seine **aufgelöste Eingabe**;
   das optionale `input`-Feld simuliert diese Eingabe (edit-the-pin).

**Demonstriert:** Debugging auf Basis der Resume-Mechanik (jeder Befehl setzt vom Checkpoint
fort, führt einen Knoten aus, pausiert erneut); Sessions, die länger als
`GRAPH_WORKFLOW_DEBUG_MAX_PAUSE` (Standard 1 h) pausiert sind, werden abgebrochen.

### 19. Der Workflow wird zum Werkzeug — als Tool veröffentlichen + `chat`-Trigger (Phase 9)

**Ziel:** einen Workflow aus `llm.agent`, aus dem Chat und von externen MCP-Clients aufrufbar
machen.

**Als Tool (9.1):** gib dem Workflow einen **Eingabevertrag** (Run-Panel → *Verträge*), setze
**Als Tool veröffentlichen** und **aktiviere** ihn. Er wird zu `workflow__<id>`, aufrufbar
aus den `llm.agent`/`tool.*`-Knoten anderer Workflows und aus dem Chat; jeder Aufruf ist ein
normaler Run (Metriken + Audit). Tiefenlimit `GRAPH_WORKFLOW_TOOL_MAX_DEPTH` (Standard 3).

**Als Chatbot (9.3):**
- **Graph:** `chat → llm.completion → chat.reply`
- `reply` (`chat.reply`) — `text`: `={{ $node.<llm>.output.content }}`.
- Aufruf: `POST /v1/graph-workflows/{id}/chat` mit `{ "message": "hallo", "session_id": "s1" }`.
  Der Graph erhält `$trigger = {session_id, message, history}` und die Session bleibt über
  Turns hinweg erhalten (Bereinigung nach `GRAPH_WORKFLOW_CHAT_SESSION_TTL`).

**Über MCP (9.2):** derselbe Workflow ist aus Claude Desktop/IDE via
`POST /v1/graph-workflows/mcp` erreichbar (JSON-RPC 2.0: `initialize` / `tools/list` /
`tools/call`).

**Demonstriert:** Workflow-als-Tool mit Anti-Rekursion, `chat`-Trigger + `chat.reply` mit
Session-Zustand, den Produkt-MCP-Server.

### 20. Planung, SLA und Navigator (Phase 17)

Dutzende Workflows betreiben, ohne sie zu bewachen. Alles wird am Workflow über
`PATCH /v1/graph-workflows/{id}` konfiguriert:

- **Kalender & Fenster (17.1):** Gib dem `schedule`-Trigger eine Zeitzone (`"tz": "Europe/Rome"`),
  damit jede Planung in ihrer eigenen Zone feuert. Feiertage überspringen mit
  `"skip_dates": ["2026-12-25"]` (an der Planung oder am Workflow). Sperrfenster am Workflow:
  `blackout = {"windows": [{"start":"01:00","end":"02:30","days":[0,1,2,3,4]}], "on_conflict":"defer"}`
  — ein während des nächtlichen Deploys fälliger Lauf wird übersprungen (`skip`, rückt zum nächsten
  Takt) oder aufgeschoben (`defer`, wiederholt, bis das Fenster frei ist). Ein `end <= start` läuft
  über Mitternacht.
- **SLA-Monitore (17.2):** `sla = {"max_duration_s":120, "missed_grace_s":900, "channels":["inapp"]}`.
  Du bekommst eine einmalige Warnung, wenn ein Lauf `max_duration_s` überschreitet oder eine aktive
  Planung über `missed_grace_s` überfällig ist (der Lauf startete nie — der blinde Fleck des
  `error`-Triggers).
- **Navigator (17.3):** `folder`, `tags` und `archived` an Workflows.
  `GET /search?q=slack&tag=billing&folder=finance&include_archived=false` sucht per Volltext über
  Name, Beschreibung **und Knoteninhalt**; `GET /folders` listet den Ordnerbaum.
- **Lauf-Vergleich (17.4):** `GET /runs/compare?a=<run>&b=<run>` — Status/Dauer/Ausgabe pro Knoten
  zweier Läufe und der **erste abweichende Knoten** („warum lief es gestern?").
- **Benachrichtigungs-Digest (17.5):** `notify = {"digest": {"enabled":true, "interval_s":86400,
  "channel":"inapp"}}` — eine tägliche Zusammenfassung (Zählung nach Ergebnis) statt einer Nachricht
  pro Lauf; `error`/`waiting`-Warnungen bleiben sofort.

**Beispiel:** die kuratierte Vorlage **Nightly report with blackout & digest** liefert den Graphen;
wende die obigen Einstellungen an, um sie zu vervollständigen.

## API

Alles, was die UI tut, ist unter `/v1/graph-workflows` (JWT-geschützt) verfügbar. Siehe den
[Entwicklerleitfaden](../developer-guide.md) für die vollständige Endpoint-Referenz.

Einstellungen: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (standardmäßig an) aktiviert die Poll-Schleife;
`GRAPH_WORKFLOW_MAX_NODES` begrenzt die Graphgröße; `GRAPH_WORKFLOW_FILES_DIR` ist die
Wurzel des Workspace-Speichers für `file.*` / sqlite-`db.query` (Phase 4.2);
`GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` begrenzt die Wartezeit eines
`human.approval`-/`human.input`-/`wait.event`-Knotens (Phase 4.4/10, Standard 7 Tage).
Phase 12: `GRAPH_WORKFLOW_BUDGET_WARN_PCT` (Standard 0,8) ist der Nutzungsanteil, der die
Budget-Warnbenachrichtigung auslöst; `GRAPH_WORKFLOW_RUNS_RETENTION_DAYS` (Standard 0 =
für immer aufbewahren) ist die instanzweite Aufbewahrungsvorgabe, die die Einstellung
eines einzelnen Workflows überschreiben kann.

## Phase 19 — Custom Node SDK

Erweitern Sie die Palette selbst. Ein **benutzerdefinierter Knoten** ist ein Paket mit
einem `node.json`-**Manifest** (`type` — immer `custom.<name>`, `name`, `category`,
`params`/`outputs`-JSON-Schemas, `handles`, `secrets`, `permissions`, `kind`) in zwei Stufen:

- **declarative** — kein Code: eine parametrisierte `http.request`-Vorlage mit
  Platzhaltern `{{param.x}}` / `{{input}}`. Von Natur aus sicher; Retry, Rate-Limit und
  Pins gelten wie bei einem kuratierten Konnektor.
- **python** — ein Modul mit `run(params, input, ctx)`, das **immer** im
  Sandbox-Subprozess läuft (kein Netzwerk, CPU-/Speicher-/Zeitlimits). `ctx` bietet nur
  die deklarierten Secrets (`ctx.secrets`) und `ctx.log` — niemals den Vault.

Hochgeladene Pakete sind versioniert (die höchste Version ist aktuell); ein aktivierter
Knoten erscheint mit *custom*-Badge in der Palette. Das Löschen eines Typs ist gesperrt,
solange ein Workflow ihn nutzt. Optional kann pro Instanz eine HMAC-**Signatur**
verlangt werden. Erstellung per CLI: `sibyl-wf node init|test|pack|push`.

```
GET/POST /v1/graph-workflows/custom-nodes            (Liste / Installation)
GET      /v1/graph-workflows/custom-nodes/{type}     (Detail, mit Code)
GET/POST /v1/graph-workflows/custom-nodes/{type}/versions
PATCH    /v1/graph-workflows/custom-nodes/{type}     ({ enabled })
DELETE   /v1/graph-workflows/custom-nodes/{type}     (409 + Abhängige bei Nutzung)
```

Einstellungen: `GRAPH_WORKFLOW_CUSTOM_NODES_DIR`, `GRAPH_WORKFLOW_REQUIRE_SIGNED_NODES`,
`GRAPH_WORKFLOW_NODE_SIGNING_KEY`.

## Phase 20 — Telegram als Workflow-Kanal

Telegram wird zu einem **bidirektionalen** Kanal, nicht nur einer Benachrichtigungssenke:

- **`telegram`-Trigger + `/run`-Launcher** — binden Sie einen Bot-Befehl (`/report`) an
  einen Workflow oder starten Sie jeden aktiven Workflow aus dem Chat mit `/run`.
  `$trigger = {chat_id, thread_id, user, text, command, args, launched_via, file?}`; die
  finale `chat.reply`/`telegram.*`-Ausgabe geht an den Chat zurück.
- **`telegram.send` / `sendMedia` / `editMessage` / `deleteMessage`** — an jeden Chat
  (`chat_id` standardmäßig `$trigger.chat_id`). Ohne Telegram sauberes No-op.
- **`telegram.ask`** — Inline-Buttons anzeigen, den Lauf aussetzen (nutzt die
  `wait.event`-Korrelation), mit dem gewählten Wert auf `main` fortsetzen (Timeout → `timeout`).
- **Eingehende Medien** — ein Dokument/Foto an einem `telegram`-Trigger wird in den
  Workspace-Speicher geholt und auf `$trigger.file` für `file.*` / `doc.convert` /
  `kb.search` bereitgestellt (Limit `GRAPH_WORKFLOW_TELEGRAM_MAX_FILE_MB`).
- **Bot-Bindungen** — `GET/POST/DELETE /v1/graph-workflows/telegram-bindings`
  (Befehlskollisionen pro Profil abgelehnt); gebundene Befehle werden beim Start per
  `setMyCommands` veröffentlicht.
