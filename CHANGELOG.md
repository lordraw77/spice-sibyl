# Changelog

All notable changes to SpiceSibyl are documented in this file.

**Nota sui tag git.** Le versioni **3.1.0, 3.2.0, 3.3.0, 3.4.0 e 3.5.0 non hanno un tag git
corrispondente** e non sono mai state rilasciate come commit distinti: tutto il lavoro fra
`v3.0.0` e `v3.6.0` è confluito in un unico commit (`a4ed227`, 22k righe), che è quello taggato
`v3.6.0`. Le sezioni 3.1.0–3.4.0 qui sotto descrivono quindi tappe di sviluppo, non release
taggate; **3.5.0 non ha nemmeno una sezione propria** perché il suo contenuto (Phase 49 —
scheduling, SLA e scale UX) è documentato dentro `[3.6.0]`. Taggare a posteriori quelle versioni
significherebbe puntare cinque tag allo stesso commit di `v3.6.0`: si è preferito un buco
dichiarato a un tag falso. Dalla `v3.6.0` in poi ogni release ha il suo tag.

---

## [Unreleased]

### Security
- **SSRF via redirect (audit 1.3)** — `assert_public_url` vagliava solo l'URL digitato dal chiamante, poi tutti i client giravano con `follow_redirects=True`: un host pubblico legittimo poteva rispondere `302 Location: http://169.254.169.254/…` e httpx lo seguiva con gli header (e i `$secrets`) del chiamante attaccati. Il guard e una versione che segue i redirect vivono ora in `app/core/safe_http.py`: i client si costruiscono con `follow_redirects=False` e **ogni hop** è rivalidato prima di essere inviato, con un tetto di 5. È httpx a costruire la richiesta di redirect (`Response.next_request`), quindi i downgrade di metodo e body su 301/302/303 restano quelli della specifica. Applicato a tutti e 5 i punti in `tools/extras.py` e `tools/builtin.py`
- **Gli ultimi due IDOR (audit 2.3 e 3.1)** — `chunks`, `source` e `wiki` non dichiaravano nemmeno `resolve_profile`, e `source` restituisce il testo integrale del documento; `reembed` risolveva il profilo ma non lo confrontava mai, e lo passava a `rag_service.reembed`, quindi il solo id ri-ingeriva il documento di un altro **e glielo riattribuiva silenziosamente**. Chiusi con un unico helper `_owned_document` ora condiviso dai quattro endpoint. Tutte e 16 le route `/knowledge` sono state poi verificate una per una: sono tutte profile-scoped
- **Brute force sul login (audit 2.5)** — il limiter prendeva `get_current_user` come dipendenza, quindi per costruzione non poteva proteggere le due route che un utente autenticato ancora non ce l'hanno. `login_guard` è indipendente: finestra stretta (`RATE_LIMIT_AUTH`, default 10/minuto) per IP e per email inviata, più un lockout la cui finestra cresce col numero di fallimenti (5/min, 15/15min, 30/ora). Contare i fallimenti senza consumare un'ammissione ha richiesto `record`/`count` accanto a `try_admit`/`admit`, implementati su entrambi i backend del limiter: il lockout funziona quindi anche multi-istanza. `X-Forwarded-For` è onorato solo sotto `TRUST_PROXY_HEADERS`
- **User enumeration via timing (audit 2.6)** — `not row or not verify_password(...)` andava in short-circuit, così un'email sconosciuta saltava bcrypt e rispondeva abbastanza in fretta da enumerare la base utenti. Ora bcrypt gira su entrambi i rami, quello sconosciuto contro un hash dummy calcolato una volta all'import
- **Login falliti nell'audit log (audit 2.8)** — registrati come `login_failed` con l'email in `detail`
- Nuove variabili: `RATE_LIMIT_AUTH`, `TRUST_PROXY_HEADERS`

### Fixed
- **Perdita silenziosa di messaggi in chat (audit 4.1)** — `appendMessages`/`create` non avevano handler `error`: un salvataggio fallito lasciava lo scambio solo in memoria e un refresh lo perdeva senza dire nulla. Entrambi i percorsi — più quello create-then-append di una conversazione nuova, che aveva lo stesso buco un livello sopra — marcano ora i messaggi `unsaved`, mostrano un chip di avviso nel thread e alzano un toast con un retry che riusa la stessa closure di salvataggio. Alle risposte dell'assistente è stato assegnato un id come già l'avevano quelle dell'utente: senza, il marcatore non aveva a cosa agganciarsi

### Changed
- **Mega-componenti Angular (roadmap v2 § 3, ultimo P2)** — `graph-workflow-page` 1.533 → 1.353, `run-panel` 1.056 → 643, `settings-page` 804 → 452, `navbar` 661 → 223. Erano due problemi diversi: tre dei quattro non erano logica ma template (e in due casi fogli di stile) inline, spostati in file `.html`/`.css` fratelli come già fa il resto del codebase; solo `graph-workflow-page` era grande davvero, e da lì sono usciti `editor/graph-history.ts` (undo/redo + clipboard), `editor/auto-layout.ts` (funzione pura), `editor/data-mapping.ts` (candidati di mapping, con `translate` come parametro invece della DI) e `editor/debug-session.ts` (debugger passo-passo, con le dipendenze verso la pagina passate come callback). Verifica meccanica: build di produzione verde e insieme dei membri pubblici di classe identico prima/dopo
- **Debito tecnico P1/P2 (roadmap v2 § 3)** — quattro god object smontati, a funzionalità invariata e con un confronto meccanico prima/dopo per ognuno:
  - `api/v1/endpoints/graph_workflows.py` (2.102 righe, 83 endpoint) → package di 13 sub-router + `_common.py`; route table FastAPI identica (87 path, stesso ordine di precedenza rispetto a `/{wf_id}`)
  - `telegram/bot.py` (2.529 righe, 84 funzioni) → package di 16 moduli dietro façade; handler table del bot identica (43 handler). I contatori `_tg_*` sono ora un oggetto condiviso e `_application` ha un setter, perché `global` avrebbe dato a ogni modulo la sua copia
  - `db/graph_workflow_repository.py` (2.400 righe) → 17 moduli per aggregato + façade; stesse 128 funzioni pubbliche, strato piatto senza cicli
  - i18n frontend: cinque cataloghi paralleli → **una dichiarazione per chiave** con tutti e cinque i locali e tipo `Record<Locale, string>`, quindi un locale dimenticato è un errore di compilazione invece di un fallback silenzioso; cataloghi proiettati verificati identici chiave per chiave
  - `chat-page.component.ts` (1.711 → 1.551 righe): estratti `SpeechService`, `TelegramLinkService`, `ImageAttachmentService`

### Added
- **Migrazioni di schema versionate** — `app/db/migrations.py` con unità numerate e ledger `schema_migrations`: il boot applica solo ciò che manca al database invece di rieseguire l'intera lista ingoiando gli errori. La versione 1 è la lista storica, tollerante perché su un DB pre-ledger non si può sapere cosa fosse già applicato; dalla 2 in poi un errore ferma il boot invece di lasciare uno schema a metà. Nessuna dipendenza aggiunta (Alembic resta un'opzione per la Phase 37)
- **Coordinamento multi-istanza** — `EventBus` e `RateLimiter` diventano interfacce con backend `memory` (default, comportamento invariato) e `database`, e `app/services/coordination.py` aggiunge la leader election a lease usata dal poll loop degli schedule: con più istanze sullo stesso database non partono più N run per lo stesso trigger, il rate limit non vale più N volte e uno stream SSE servito da un'istanza vede i run eseguiti da un'altra. Nuove variabili: `RATE_LIMIT_BACKEND`, `WORKFLOW_BUS_BACKEND`, `SCHEDULER_LEADER_ELECTION`, `SCHEDULER_LEASE_TTL_SECONDS`

### Security
- **IDOR sul link Telegram (audit 2.1)** — i tre endpoint `/v1/telegram/link` prendevano il `profile_id` da body/path senza alcun controllo di proprietà: conoscere l'UUID di un profilo bastava per leggerne lo stato del link, cancellarlo o dirottarlo sul proprio account Telegram. `POST /link`, `GET /link/{profile_id}` e `DELETE /link/{profile_id}` verificano ora il profilo contro l'utente autenticato (`404` se inesistente, `403` se di un altro utente) prima di qualunque lettura o scrittura; un tentativo respinto **non** consuma il codice di link monouso
- **IDOR sulla cancellazione dei documenti KB (audit 2.2)** — `DELETE /v1/knowledge/documents/{doc_id}` cancellava documento, chunk, grafo e vettori di qualsiasi profilo. Ora la route risolve il profilo del chiamante e restituisce `404` per un documento che non gli appartiene
- 7 test di regressione (`backend/tests/test_idor.py`): utente B con token valido non può leggere/cancellare/dirottare risorse di utente A, mentre le stesse operazioni sulle proprie risorse continuano a funzionare

### Fixed
- **MCP stdio: actionable error when the server process dies before the handshake** — registering a `docker run …` MCP server whose container exited immediately (image not pulled, `docker.sock` permission denied, wrong entrypoint) surfaced a raw event-loop traceback (`RuntimeError: unable to perform operation on <WriteUnixTransport closed=True …>; the handler is closed`) and logged it as *"MCP probe crashed"*. `_StdioSession._send`/`_read_result` now translate the dead-transport `RuntimeError`/`OSError` into `MCPError`, and `_open_stdio` catches **any** handshake failure so it can always report the child's stderr plus its exit code, e.g. `handshake failed for 'docker' (exit code 125): Unable to find image …`. The MCP servers page shows that text instead of the uvloop message. See `docs/mcp-deployment.md` § Troubleshooting

---

## [3.8.0] - 2026-08-01

### Changed
- **Telegram `/tool` vs `/tools` split (23.c)** — the per-chat tool-loop toggle moves to its own command: **`/tool on|off`** enables/disables tool usage for the chat (persisted in `telegram_prefs.tools`, OFF by default), while **`/tools`** becomes **view-only** — it lists the available tools grouped by kind (🧩 built-in · 🔌 MCP · 🛠 custom) plus the current status, and never mutates state. `/tool` with no (or an invalid) argument replies with its usage. Both commands are in the bot command menu; the `/help` (`/start`) text and the five-locale bot strings were updated accordingly
- Version bumped to **3.8.0**; docs updated

---

## [3.7.0] - 2026-07-23

### Added — Phase 51 (roadmap fase 19): Custom Node SDK
- **Node manifest & packaging** (19.1) — users extend the palette themselves. A custom node is a package with a `node.json` **manifest** (`type`, `name`, `category`, `params`/`outputs` JSON Schemas, `handles`, `secrets`, `permissions`, `kind`). `custom_node_service.validate_manifest` enforces `custom.<name>` namespacing so a custom type can never collide with a builtin (`http.request`, `llm.*`) or a `tool.*` node. Two tiers: **declarative** (no code — a parameterised `http.request` template with `{{param.x}}`/`{{input}}` placeholders, rendered by the pure `build_declarative_request` mapper, so retry/rate-limit/pins apply exactly like a curated connector) and **python** (a module defining `run(params, input, ctx)`)
- **Upload, registry & lifecycle** (19.2) — new `custom_nodes` table (one row per profile+type+version; the highest version is current). CRUD at `GET/POST /v1/graph-workflows/custom-nodes`, `GET /custom-nodes/{type}`, `GET/POST /custom-nodes/{type}/versions`, `PATCH` (enable/disable) and `DELETE` (blocked with a **409 + dependent list** while any workflow references the type). Enabled custom nodes appear in the palette badged `custom: true`, their inspector fields generated from the manifest's `params` schema
- **Security model** (19.3) — declarative nodes are safe by construction; **python nodes always run in the Phase 18 code sandbox** (isolated subprocess, CPU/memory/time caps, no network). `ctx` exposes only the manifest-declared secrets (`ctx.secrets`) and `ctx.log` — never the vault. Install/version/delete are audited (fase 7.3); optional package **signing** (HMAC-SHA256) gated by `GRAPH_WORKFLOW_REQUIRE_SIGNED_NODES` + `GRAPH_WORKFLOW_NODE_SIGNING_KEY`
- **Developer experience** (19.4) — `sibyl-wf node init|test|pack|push`: scaffold a package, validate it locally against the manifest contract (rendering a declarative node's `http.request` from a fixture), sign/bundle it and upload it to the install endpoint
- **Distribution** (19.5) — a workflow's `GET /{id}/export` now lists its `custom_nodes` dependencies `{type, version}` so an import can warn on / offer to install missing packages
- 15 new backend tests (`tests/test_phase51.py`)

### Added — Phase 52 (roadmap fase 20): Telegram as a first-class workflow channel
- **`telegram` trigger + `/run` launcher** (20.1) — `telegram` is a new trigger type. The bot's `/run` command lists the sender's **active** workflows as an inline keyboard (or launches one by name/id), and a catch-all command handler routes a **bound command** (`/report`) to its workflow (registered last so builtin commands win). `run_telegram_workflow` runs the graph inline with `$trigger = {chat_id, thread_id, user, text, command, args, launched_via, file?}` and returns its terminal `chat.reply`/`telegram.*` output to the chat
- **`telegram.send` and message nodes** (20.2) — `telegram.send` / `telegram.sendMedia` / `telegram.editMessage` / `telegram.deleteMessage` send to any chat (`chat_id` defaults to `$trigger.chat_id`). Off Telegram they no-op cleanly (`sent:false`); a send that raises surfaces so On error (2.x) applies
- **Interactive inline keyboards** (20.3) — a `telegram.ask` node presents buttons and suspends the run reusing the `wait.event` correlation machinery; a tap delivers the chosen value and resumes down `main` (timeout → `timeout`), the callback clearing the client spinner and editing the prompt to show the decision
- **Inbound media ingestion** (20.4) — `save_inbound_telegram_file` fetches an inbound document/photo into `GRAPH_WORKFLOW_FILES_DIR` (size-capped by `GRAPH_WORKFLOW_TELEGRAM_MAX_FILE_MB`) and exposes it on `$trigger.file` for `file.*`/`doc.convert`/`kb.search`
- **Bot binding** (20.5) — new `telegram_command_bindings` table + `GET/POST/DELETE /v1/graph-workflows/telegram-bindings` (per-profile command collision rejected with 409); `register_workflow_bot_commands` publishes bound commands via `setMyCommands` on boot. A dedicated per-workflow bot token is the documented, deferred escape hatch
- 13 new backend tests (`tests/test_phase52.py`)

### Changed
- New `custom_nodes` and `telegram_command_bindings` tables (idempotent `CREATE TABLE IF NOT EXISTS`); `NodeTypeInfo` gained a `custom: bool` badge; `_dispatch` routes `custom.*` and `telegram.*` node types; `_EXTERNAL_EFFECT_PREFIXES` gained `custom.`/`telegram.` so pins & dry-run intercept them
- New settings: `GRAPH_WORKFLOW_CUSTOM_NODES_DIR`, `GRAPH_WORKFLOW_REQUIRE_SIGNED_NODES`, `GRAPH_WORKFLOW_NODE_SIGNING_KEY`, `GRAPH_WORKFLOW_TELEGRAM_MAX_FILE_MB`
- Version bumped to **3.7.0**; docs + `.env.example` updated

---

## [3.6.0] - 2026-07-22

### Added — Phase 42 (roadmap fase 10): Advanced human-in-the-loop
- **`human.input` node (form)** (10.1) — like `human.approval`, but the request carries a **form defined by JSON Schema** (`schema` param); the run suspends (`waiting`) until someone submits it via `POST /v1/graph-workflows/approvals/{id}/submit` (or the runs page, which renders the fields from the schema). Submitted `data` is validated against the schema (the existing dependency-free `_validate_json_schema`, fase 6.4) before it is accepted; the run resumes with `{data}` on the `submitted` branch. A timeout follows `onTimeout` (`branch` routes to the `timeout` branch, `fail` raises)
- **`wait.event` node with correlation** (10.2) — the run suspends until an external system delivers an event with a matching **correlation id**: `POST /v1/graph-workflows/events/{correlation_id}` (authenticated, profile-scoped) wakes the run and delivers its `payload` as the node's output on the `main` branch. `correlationId` is an expression (e.g. the order id from `$trigger`); same `onTimeout` semantics as `human.input`. Covers real async callbacks (payments, signatures, tickets) without polling; a `waiting` run does not occupy a concurrency slot

### Changed
- The Phase 35 `workflow_approvals` table is generalised into a `kind` (`approval|input|event`) "waiting request", with new `schema_json` (human.input's form schema) and `data_json` (submitted data / delivered payload) columns and a `correlation_id` column + index (wait.event) — idempotent migrations. All three node types share the same poll/resume loop (`_wait_for_decision`), so `human.input`/`wait.event` survive a backend restart exactly like `human.approval`
- `GET /v1/graph-workflows/approvals` gained an optional `?kind=` filter; `WorkflowApprovalOut` gained `kind`, `form_schema`, `data`, `correlation_id`
- Two new curated examples (`expense-approval-form`, `payment-webhook-wait`); the runs page renders a dynamic form for `human.input` and a correlation-id delivery box for `wait.event`
- 12 new backend tests (`tests/test_phase42.py`); docs + i18n updated in 5 languages

### Added — Phase 43 (roadmap fase 11): Workflow quality and testing
- **Workflow test suites** (11.1) — saved test cases (`workflow_test_cases`: name, fixture `$trigger` payload, assertions) under `POST/GET/PUT/DELETE /v1/graph-workflows/{id}/test-cases`; `POST /{id}/test-cases/run` ("Run tests") executes each case as a real, observable run and checks its assertions (`equals`/`contains`/`json_path`/`schema`) against the actual node outputs. External-effect nodes (`http.request`/`db.query`/`notification.*`/`email.*`/`llm.*`) carrying a fase-3.2 pinned output use it instead of the real call, so a test with a pin is deterministic; nodes without a pin still execute for real
- **Full dry-run** (11.2) — `POST /{id}/dry-run` simulates the whole graph: every external-effect node is mocked unconditionally (its pin if present, else a typed placeholder shaped like the real output), so nothing external ever happens. Returns the execution path, every node's simulated output and the list of nodes a real run would have had a side effect on — use it before activating a schedule on a new graph
- **Pre-run cost estimate** (11.3) — `GET /{id}/cost-estimate` projects tokens/month from the graph's `llm.*` node count, the historical average tokens per run (from the fase 7.4 per-node stats) and the workflow's active schedule fire frequency (derived from `reminder_parsing.compute_next_fire`). Tokens only, no invented price list; the `basis` field always explains what the figure does and does not account for (missing run history, no active schedule, …)
- A new **Tests & dry-run** section in the run panel: manage test cases, run the suite and see pass/fail per assertion, trigger a dry-run and read its report, and see the cost estimate — all without leaving the editor
- A new curated example, `http-mock-pin-demo` — an `http.request` node shipped with a pinned output, ready to exercise "Run tests" / "Dry-run" without a live endpoint

### Changed
- New `workflow_test_cases` table (idempotent migration)
- `_run_node` gained a mock-dispatch interception point (`_mock_dispatch`/`_is_external_effect`) shared by 11.1 and 11.2; `run_workflow_sync`/`_execute` gained `use_pins`/`dry_run` parameters
- 10 new backend tests (`tests/test_phase43.py`); docs + i18n updated in 5 languages

### Added — Phase 44 (roadmap fase 12): Data and budget governance
- **Budgets and quotas** (12.1) — `token_budget_month`/`run_budget_month` caps on the workflow (`GraphWorkflowCreate`/`Update`/`Out`) plus a profile-wide ("workspace") cap under `GET/PUT /v1/graph-workflows/budget` (new `profile_budgets` table). Usage is derived on the fly from the existing fase 5.1 stats sources (`workflow_runs`/`workflow_node_runs`), time-boxed to the current UTC calendar month, rather than duplicated in a counter — a period "resets" for free. `run_workflow()` checks both caps before spawning a run (skipped for partial/dev and step-debug runs); a cap fully reached raises `BudgetExceededError` — a manual/API call is rejected with an explicit 400, and a schedule/event trigger firing is caught by the existing Phase 30.b consecutive-failure counter and auto-disables past the configured threshold. Crossing `GRAPH_WORKFLOW_BUDGET_WARN_PCT` (default 0.8) of either cap fires a one-time in-app soft warning per period. `GET /{id}/budget` reports the workflow's own usage plus the profile-wide one it is also gated by
- **Run log retention and redaction** (12.2) — a per-workflow `runs_retention_days` overriding the new global `GRAPH_WORKFLOW_RUNS_RETENTION_DAYS` default (0 = keep forever); the scheduler sweep purges terminal (`completed`/`failed`/`cancelled`) runs — and their cascaded node runs — past the cutoff, never touching `queued`/`pending`/`running`/`waiting`/`paused` runs. A new `redact: string[]` field on `GraphNode` (dotted JSON paths, e.g. `body.card_number`) masks matching leaves as `"***"` in the persisted `workflow_node_runs` output, the live SSE event and a pinned output carried by export/sharing — the live run context keeps the real value so downstream node expressions still resolve it in cleartext. `$secrets` remain never serialized regardless (fase 1.3)
- Editor UI: a **Budget & quotas** subsection in the run panel's quality/governance details block (caps + live usage, editable) and a **Redact** field in the node inspector's Advanced section (comma-separated paths)

### Changed
- New `profile_budgets` table; `workflows` gained `token_budget_month`, `run_budget_month`, `budget_warned_period`, `runs_retention_days` columns (idempotent migrations)
- `GraphNode` gained a `redact: list[str]` field; the export endpoint runs a pinned output through it before the definition leaves the system
- 12 new backend tests (`tests/test_phase44.py`); docs + i18n updated in 5 languages; `.env.example`

### Added — Phase 45 (roadmap fase 13): Copilot and workflow-as-code
- **Expression autocomplete** (13.1) — a frontend-only, framework-free `getSuggestions(text, cursor, ctx)` wired into the node inspector's expression fields and the expression tester: `$node.` proposes the ids of nodes upstream of the one being edited (a backward BFS over the current canvas edges, no server round-trip needed), then completes with that node's known output fields (from a fase-3.2 pinned output or its last run's live output); `$vars.`/`$secrets.` complete against the workflow's declared variables and the profile's secret *names* (never values); a bare `$` also offers `$item`/`$index` when the field belongs to a node reachable from a for/repeat's `loop` handle
- **"Explain / repair" with LLM** (13.2) — `POST /v1/graph-workflows/runs/{run_id}/explain`: the run's first failed node (type, catalog entry, current params, input, error) goes to the LLM via the same `_llm_json_call` the fase-5.3 generator uses, asking for `{explanation, proposed_params}`. `proposed_params` is optional — the model returns `null` rather than guess when unsure — and is never applied automatically; the backend only computes a display-only add/remove/replace diff against the node's current params. The run panel shows an **Explain / repair** button on any failed node, renders the explanation and diff, and an **Accept** button merges the proposed params into the node in the editor (still requires a normal save) while **Discard** drops it
- **Git sync of definitions** (13.3) — new `git_repo_url`/`git_branch`/`git_token_secret`/`git_subpath`/`git_last_synced_at` columns on `workflows`; `PUT /{id}/git-sync` configures sync (an empty `repo_url` disables it — `token_secret` names a `$secrets` entry, its value never appears in the API). Every subsequent saved version shells out to `git` (plain subprocess calls, no Python git dependency) to clone-or-fetch a per-workflow local working copy (`GRAPH_WORKFLOW_GIT_WORKDIR`), write the fase-5.2 export envelope to `<subpath|workflows/<id>.json>`, commit `"<name> v<version> (by <email>)"` and push — best-effort, a broken remote only logs a warning and never fails the save. `POST /{id}/git-sync/pull` fetches the branch and, when the file's graph differs from the latest known version, adds it as a new **draft** `workflow_versions` row (never touches the live graph) — reviewed/restored/diffed like any other version (fase 1.4/8.1). Requires the `git` CLI, now installed in `backend/Dockerfile`
- Editor UI: a **Git sync** subsection next to Versions in the run panel (repo URL/branch/token secret/path, Save/Pull now/Disable), and an **Explain / repair** action + accept/discard diff under each failed node in the run status list

### Changed
- New `WorkflowExplainOut`/`WorkflowGitSyncIn`/`WorkflowGitSyncOut`/`GitSyncPullOut` schemas; `GraphWorkflowOut` gained a `git_sync` field
- `add_draft_version`/`set_git_sync`/`mark_git_synced` repository helpers; `git_sync_push_version`/`git_sync_pull`/`explain_run` engine functions
- 8 new backend tests (`tests/test_phase45.py`, including two exercising real `git` push/pull against a local bare repo); docs + i18n updated in 5 languages; `.env.example`

### Added — Phase 46 (roadmap fase 14): Remote execution and scalability
- **Remote runners** (14.1) — new `workflow_runners`/`workflow_runner_jobs` tables. `POST /v1/graph-workflows/runners` provisions a runner slot (`{name, labels, allowed_node_types}` → a one-time raw token, hashed at rest); `GET`/`DELETE` list/revoke. The agent process (`python -m app.runner.agent`, `SIBYL_RUNNER_TOKEN`) authenticates via `X-Runner-Token` (not a user session) against three public endpoints: `POST /v1/wf/runners/heartbeat`, long-polling `GET /v1/wf/runners/jobs/next`, and `POST /v1/wf/runners/jobs/{id}/result` — the fase-3.1 `test_node()` `{ok, output, handles, logs}` contract. A new `runOn` (label) + `runOnFallback` (`fail`|`local`) field pair on `GraphNode` routes a **stateless-safe** subset of node types (`http.request`, `code`, `db.query`, `set`, `if`, `switch`, `merge`, `filter`, `aggregate`, `batch`, `wait`, `queue.publish` — `_REMOTE_CAPABLE_TYPES`) to the first online, allow-listed runner carrying that label; `$secrets` referenced in the node's params reach the runner already resolved to literal values, never the vault. A missing/unanswering runner within the job timeout either fails the node (subject to its ordinary retry/onError) or falls back to local execution, per `runOnFallback`. A minimal **Runners** page (`/graph-workflows/runners`) lists online/offline, labels, allowed types, version, registers new runners (token shown once) and revokes existing ones
- **`code` node sandbox** (14.2) — already satisfied: the `code` node has always dispatched through the Phase 18 `python_exec` sandboxed subprocess (CPU/memory/wall-clock limits, no network); a remote runner claiming a `code` job reuses the identical `_dispatch_stateless` path, so isolation is unchanged either way
- **Engine scale-out** (14.3) — new `lease_owner`/`lease_expires_at` columns on `workflow_runs`; `repo.acquire_lease` is a single conditional `UPDATE` (succeeds when unleased, already owned by the caller, or expired) that `_execute` claims before doing any work, renews on every checkpoint and releases on completion — a run whose lease is held by another live instance is skipped outright (no double-execution). Generic and inert on today's single-process SQLite deployment; needs no Postgres migration to exist, though true multi-replica coordination still does
- **Message queue triggers** (14.4) — a pluggable `QueueDriver` ABC (`publish`/`consume`) with two shipped drivers selected by `GRAPH_WORKFLOW_QUEUE_DRIVER`: `db` (default) persists in a new `workflow_queue_messages` table, `memory` is per-process (tests/dev). New `queue.publish` node; `queue.consume` trigger reuses the existing file.watch/email.inbound poll loop (`list_due_poll_triggers`, `_poll_queue_consume`), firing one run per message with `$trigger = {message, topic, headers}`. No AMQP/Kafka/MQTT client ships (unavailable/untestable in this environment) — a real broker plugs in as a third `QueueDriver` implementation without touching the node/trigger/poll-loop code
- **CLI** (14.5) — `python -m app.cli.sibyl_wf {run|export|import|test|logs}`, an `httpx.AsyncClient`-based client over the existing REST API (`SIBYL_API_URL`/`SIBYL_API_KEY`/`SIBYL_PROFILE_ID`), for CI and UI-less operations

### Changed
- New `workflow_runners`, `workflow_runner_jobs`, `workflow_queue_messages` tables; `workflow_runs` gained `lease_owner`/`lease_expires_at` (idempotent migrations)
- `GraphNode` gained `runOn`/`runOnFallback`; `queue.consume` added to the trigger-type enum
- `_dispatch_stateless`/`_dispatch_remote` in `workflow_graph_service.py`; new `app/runner/agent.py` and `app/cli/sibyl_wf.py` modules
- 25 new backend tests (`tests/test_phase46.py`); docs + i18n updated in 5 languages; `.env.example`; new **Runners** page

### Added — Phase 47 (roadmap fase 15): Connectors and multimodal nodes
- **Curated connector library** (15.1) — prebuilt `connector.<service>.<operation>` nodes (a new **Connectors** palette category) implemented over `http.request` with auth, endpoints and payloads pre-wired: `slack.postMessage`, `discord.postMessage`, `github.createIssue`, `gitlab.createIssue`, `jira.createIssue`, `sheets.append`, `sheets.read`. Credentials come from `$secrets` (e.g. `token = ={{ $secrets.SLACK_TOKEN }}`); each operation is a one-line entry in the `_CONNECTORS` registry (mapper → an `http.request` spec), so retry/backoff (2.1), node test (3.1), pins (3.2) and rate-limiting (6.6) all apply for free. Output is the `http.request` output plus `{operation}`
- **`ssh.exec` node** (15.2) — runs a command on a remote host over SSH (paramiko; key or password from `$secrets`), output `{stdout, stderr, exit_code}`, per-command timeout and a per-instance host allow-list (`GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS`, empty = any). A non-zero exit raises (so retry / On error apply) unless `allow_nonzero` is set
- **`browser` node** (15.3) — headless-browser scraping/checks (Playwright): open a URL, optionally wait for a selector, then extract text / an attribute / a screenshot (saved to the workspace storage, readable by `file.*`). Runs in a worker thread with a per-action timeout; a missing Playwright raises a clear error instead of degrading silently
- **`rss.read` trigger** (15.4) — polls an RSS/Atom feed (dependency-free `xml.etree` parse of both formats) and fires one run per new entry, deduped by guid, `$trigger = {title, link, published, summary, guid}`. Reuses the file.watch/email.inbound poll loop (`list_due_poll_triggers`); the first poll only seeds the seen-set so a backlog never storms the engine (`GRAPH_WORKFLOW_RSS_MAX_ENTRIES` caps fires per poll)
- **`doc.convert` node** (15.5) — converts a PDF/DOCX/HTML/PPTX/… document from the workspace storage to markdown via markitdown (already in the backend image for the KB), output `{markdown, chars, path}`; `path` defaults to the node input (e.g. a `file.watch` `$trigger.path`). The remaining multimodal media nodes (`audio.transcribe`, `image.ocr`, `image.generate`, `tts`) depend on provider-layer support and are deferred

### Changed
- `rss.read` added to the trigger-type enum (`TRIGGER_TYPES`, `WorkflowTriggerCreate` pattern, `list_due_poll_triggers` SQL) and the create-trigger endpoint validates its `url`; new node-catalog entries + a **Connectors** palette category (colour + i18n label in 5 languages)
- New settings `GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS` / `GRAPH_WORKFLOW_SSH_TIMEOUT_SECONDS` / `GRAPH_WORKFLOW_BROWSER_TIMEOUT_SECONDS` / `GRAPH_WORKFLOW_RSS_MAX_ENTRIES` (`config.py` + `.env.example`)
- 18 new backend tests (`tests/test_phase47.py`); docs + i18n updated in 5 languages; a new `rss-to-telegram-digest` example

### Added — Phase 48 (roadmap fase 16): State and execution semantics
- **Persistent state across runs** (16.1) — `state.get` / `state.set` / `state.increment` nodes over a per-workflow key/value store (new `workflow_state` table, JSON values, optional per-key TTL) that survives across runs: counters, pagination cursors, "last processed id". `state.get` returns `{key, value, found}` (with an optional `default`); `state.set` defaults `value` to the node input; `state.increment` is atomic (SQLite single writer) and returns the new number. The store is viewable/editable from the run panel via `GET/PUT/DELETE /v1/graph-workflows/{id}/state` (manual edits audited, fase 7.3) and is **excluded from export** by design (separate table). Expired keys read as absent (lazy expiry) and are reclaimed by the scheduler sweep
- **Trigger idempotency** (16.2) — a `dedupKey` expression on a webhook/event trigger (e.g. `{{ $trigger.order_id }}`) deduplicates repeated deliveries: the same key delivered twice inside `dedupWindowSeconds` returns the original `run_id` (HTTP 200, `deduped: true`) instead of starting a second run. Keys are stored with a TTL in the new `workflow_trigger_dedup` table; the default window is `GRAPH_WORKFLOW_DEDUP_DEFAULT_WINDOW_SECONDS`. Essential with external systems that retry deliveries (payment webhooks, message queues)
- **Compensations / saga** (16.3) — an opt-in `compensate` handle on a side-effecting node: when the run fails downstream, the engine walks the completed nodes in reverse order and runs the subgraph hanging off each node's `compensate` edge, seeded with that node's own output (e.g. release the reserved stock when the later charge fails). Compensation node runs are tagged `compensation: true` on the live SSE stream; a failure inside a compensation marks the run `failed` with a compound error. No behavior change for existing graphs (a graph with no `compensate` edge never triggers it)
- **Run priority** (16.4) — a `priority` on runs (from the trigger config `priority` or the launch API `priority`): the per-workflow queue (2.3) promotes higher-priority runs first, FIFO within the same priority, so an interactive run can jump ahead of a batch backfill

### Changed
- New `workflow_state` and `workflow_trigger_dedup` tables; `workflow_runs` gained a `priority` column (idempotent migrations); `next_queued_run` orders by `priority DESC, created_at ASC`
- `run_workflow()` gained a `priority` parameter; new `run_from_trigger()` centralises trigger idempotency + priority for the webhook receiver and event dispatch; `GraphRunOut`/`RunTriggerIn` gained `priority`, plus `WorkflowStateIn`/`WorkflowStateOut` schemas
- Three new node-catalog entries (`state.get`/`state.set`/`state.increment`) in the **Data** category; new settings `GRAPH_WORKFLOW_STATE_DEFAULT_TTL_SECONDS` / `GRAPH_WORKFLOW_DEDUP_DEFAULT_WINDOW_SECONDS` (`config.py` + `.env.example`)
- 13 new backend tests (`tests/test_phase48.py`); a new curated example `idempotent-order-saga`; docs + i18n updated in 5 languages

### Added — Phase 49 (roadmap fase 17): Scheduling, SLA and scale UX
- **Calendars and windows** (17.1) — **per-schedule timezone** (`tz` on a `schedule` trigger config, so one workflow can carry schedules in several zones); **holiday skip dates** (`skip_dates: ["YYYY-MM-DD"]` on the schedule or the workflow) and workflow-level **blackout windows** (`blackout.windows: [{start:"HH:MM", end:"HH:MM", days:[0-6]?}]`, an `end <= start` wrapping past midnight), all evaluated in the schedule's own timezone. A schedule due inside a window / on a skip date is not run: `blackout.on_conflict` picks `"skip"` (advance to the next recurrence) or `"defer"` (retry until the window clears). Decided before the run is even created
- **SLA monitors** (17.2) — per-workflow `sla: {max_duration_s, missed_grace_s, channels}`. A scheduler sweep raises a **one-time** alert when a run overruns `max_duration_s`, or when an enabled schedule is overdue past `missed_grace_s` (the run never started — the blind spot the `error` trigger can't see). Deduped by a `sla_alerted` flag on the run and `last_sla_alert_at` on the trigger; routed to `inapp`/`telegram`
- **Folders, tags and search** (17.3) — `folder`, `tags` and `archived` on workflows; `GET /v1/graph-workflows/search` full-text over name, description **and node contents** (`q=slack` finds a workflow that merely uses a Slack node), filtered by `folder`/`tag`, archived hidden unless `include_archived=true`; `GET /folders` lists the folder tree
- **Run comparison** (17.4) — `GET /v1/graph-workflows/runs/compare?a=&b=` diffs two runs of one workflow: per-node `status`/`duration_ms`/`output` on each side, `output_equal` per node and `first_divergent_node` ("why did it work yesterday?"). Divergent payloads only are carried, keeping the diff light. Complements the version diff (8.1), which compares definitions rather than executions
- **Notification digest** (17.5) — per-workflow `notify.digest: {enabled, interval_s, channel}`: each terminal run buffers one row in the new `workflow_notification_digest` table instead of an immediate message; a scheduler sweep delivers one summary per `(workflow, channel)` — counts by outcome — once the bucket's oldest entry ages past `interval_s`, then clears it. Opt-in, so workflows that didn't ask are untouched; `error`/`waiting` alerting stays immediate

### Changed
- New `workflow_notification_digest` table; `workflows` gained `blackout_json`/`sla_json`/`notify_json`/`folder`/`tags_json`/`archived`, `workflow_runs` gained `sla_alerted`, `workflow_triggers` gained `last_sla_alert_at` (idempotent migrations)
- The schedule poll loop now consults `_schedule_blocked` (per-schedule tz + blackout) before firing, and the scheduler tick runs `check_sla_monitors` and `flush_notification_digests`; `run_workflow_sync`/`_execute` finalization buffers the outcome for the digest (`_record_run_outcome`); new `compare_runs` service function
- `GraphWorkflowCreate`/`Update`/`Out` gained `blackout`/`sla`/`notify`/`folder`/`tags`/`archived`; new `RunCompareOut`/`RunCompareNode` schemas; repository `search_workflows`/`list_folders` + SLA/digest helpers
- 11 new backend tests (`tests/test_phase49.py`); a new curated example `nightly-report-blackout`; docs + i18n updated in 5 languages

### Added — Phase 50 (roadmap fase 18): LLM quality
- **`llm.judge` node** (18.1) — evaluates content (another node's output by default) against a **rubric** (`criteria`) on a 1..`scaleMax` scale and routes to the **`pass` / `fail` handle** by a `threshold` (default 60% of the scale). Output `{score, verdict, passed, rationale}`. The score/threshold decides `passed` **authoritatively** (even when the model's own `verdict` disagrees), so a *generate → judge → regenerate* loop (wire `fail` back through `while`, 6.3) or a quality gate before publishing has a deterministic gate. Shares the model picker, failover chain and response cache with the other `llm.*` nodes; the judge model can differ from the generator's. A non-numeric score or missing `criteria` raises, so retry / On error apply
- **Prompt A/B testing** (18.2) — any `llm.*` node can carry `variants` (`[{name, weight?, params:{overrides}}]`) alternated across runs by `variantStrategy`: **round-robin** (default, a per-node counter persisted in `workflow_state`, so it survives restarts) or **weighted** (sampled by each variant's weight). The chosen variant's params overlay the node's own, and the choice is stamped on the node output (`_variant`). `GET /v1/graph-workflows/{id}/nodes/{node_id}/variants` breaks the run history down **per variant** — executions, ok-rate, mean `llm.judge` score, pass-rate, tokens — and flags the leading variant `winner` (highest avg score, else ok-rate), the basis for a "promote variant" decision

### Changed
- New engine node `llm.judge` (`_exec_llm_judge`) with `pass`/`fail` handles + dry-run placeholder; `_run_node` selects an A/B variant once per node run (`_select_variant`/`_variant_list`, before retries so the round-robin counter advances once) and records `_variant` on the output; A/B params advertised on the `llm.*` catalog entries
- New setting `GRAPH_WORKFLOW_JUDGE_DEFAULT_SCALE_MAX` (default 5); new `WorkflowNodeVariantStatsOut` schema and `variant_stats_for_node` repository aggregate; new `GET /{id}/nodes/{node_id}/variants` endpoint
- 11 new backend tests (`tests/test_phase50.py`); a new curated example `llm-quality-gate` (generate → judge → pass/fail gate); `.env.example` + docs updated

---

_The latest tagged release is [3.4.0]._

---

## [3.4.0] — 2026-07-20

### Added — Phase 41 (roadmap fase 9): Workflows as ecosystem tools
- **Workflow exposed as a tool** (9.1) — a new `expose_as_tool` flag on the workflow: when it is **active** and declares an input contract (fase 6.4), it is published as a callable tool namespaced `workflow__<id>` — available to `llm.agent` nodes, other workflows' `tool.*` nodes and the product chat (`workflow_tool_service`, routed by `registry.execute_tool`). Invocation runs the workflow inline as a first-class run (queue, stats and audit apply) and returns its sink output as the tool result. An **anti-recursion depth guard** (contextvars counter, `GRAPH_WORKFLOW_TOOL_MAX_DEPTH`, default 3) caps the tool→workflow→tool chain so a self-referential workflow can't recurse forever. `GET /v1/graph-workflows/tools` lists the published tools (name/description/JSON-Schema params derived from the contract); a **"Publish as a tool"** toggle sits in the run panel's Contracts section
- **Workflow MCP server** (9.2) — `POST /v1/graph-workflows/mcp` is the product's own MCP server: a JSON-RPC 2.0 endpoint (streamable-HTTP transport) that publishes the same `expose_as_tool` workflows to **external MCP clients** (Claude Desktop, IDEs). Implements `initialize` / `tools/list` / `tools/call` / `ping` and the `notifications/*` no-ops; auth is the caller's normal credential. A `tools/call` runs the workflow inline (trigger origin `mcp`) and returns its output as MCP `content` (`workflow_mcp_service`)
- **`chat` trigger + `chat.reply` node** (9.3) — turns a workflow into a chatbot: `POST /v1/graph-workflows/{id}/chat` (`{session_id?, message}`) runs the workflow with `$trigger = {session_id, message, history}` and returns the terminal `chat.reply` node's text as `reply`. Session state persists across turns in the new `workflow_chat_sessions` table (rolling history trimmed to `GRAPH_WORKFLOW_CHAT_HISTORY_MAX_TURNS`; idle sessions purged past `GRAPH_WORKFLOW_CHAT_SESSION_TTL` by the scheduler sweep). `chat` is a new trigger type; `chat.reply` a new catalog node
- **OpenAPI import** (9.4) — `POST /v1/graph-workflows/openapi/import` (inline `spec` or `url`, optional `path_prefix`) parses an OpenAPI/Swagger spec into preconfigured `http.request` node drafts, one per operation: method, URL (server + path), query parameters, and auth mapped onto `$secrets` placeholders (bearer → `Authorization: Bearer {{ $secrets.API_TOKEN }}`, apiKey header → that header). Nothing is saved — the editor drops the returned nodes onto the canvas. Capped by `GRAPH_WORKFLOW_OPENAPI_MAX_OPERATIONS` (`openapi_import_service`)

### Changed
- `workflows` gained an `expose_as_tool` column (idempotent migration); `GraphWorkflow` (create/update/out), export and import carry the flag; the frontend `GraphWorkflow` model + run-panel toggle expose it
- New `workflow_chat_sessions` table; `TRIGGER_TYPES` gained `chat`; the node catalog gained the `chat` trigger and `chat.reply` nodes
- `execute_tool` now routes `workflow__<id>` names; `_full_tool_definitions` (llm.agent's tool set) includes exposed workflow tools
- New settings `GRAPH_WORKFLOW_TOOL_MAX_DEPTH`, `GRAPH_WORKFLOW_CHAT_SESSION_TTL`, `GRAPH_WORKFLOW_CHAT_HISTORY_MAX_TURNS`, `GRAPH_WORKFLOW_OPENAPI_MAX_OPERATIONS` (`.env.example` updated)
- 13 new backend tests (`tests/test_phase41.py`); docs + i18n updated in 5 languages

---

## [3.3.0] — 2026-07-20

### Added — Phase 40 (roadmap fase 8): Advanced editor
- **Visual diff between versions** (8.1) — `GET /v1/graph-workflows/{id}/versions/{a}/diff/{b}` returns a structural diff of two saved graph versions: nodes grouped as added / removed / changed / unchanged (with each changed node's config `before`/`after`) plus edge deltas. A node's **position is deliberately excluded** — moving a node is not a change. The editor paints the current canvas (added green, changed yellow) and shows a diff bar with counts + the removed-node list; a **compare** row in the run panel's Versions section drives it (defaults to previous → current). With environments (7.2) it answers "what changes when promoting to prod"
- **Notes and frames on the canvas** (8.2) — `WorkflowGraph` gained a `notes` array of sticky notes and frames: rendered on the SVG canvas (frames behind everything, notes on top), draggable, double-click to edit (empty text deletes), added from the toolbar (📝 Note / ▢ Frame). They are saved with the graph, versioned and carried by export/import, but the **engine never reads them** (`_execute` only ever iterates `nodes`/`edges`) — purely presentational
- **Step-by-step debugging** (8.3) — `POST /{id}/run` with `debug:true` creates the run in a new **`paused`** status without executing any node; `POST /runs/{id}/debug` advances it: `step` runs the next node then pauses again, `continue` runs to the next breakpoint (or the end), `stop` cancels. Breakpoints are set by clicking a node's dot in debug mode; the paused run exposes its `pending_node` and the resolved input, and an optional `input` override mocks the next node's primary input (edit-the-pin). Built on the fase 2.4 resume machinery — each command re-spawns execution from the checkpoint, runs one node/segment and re-pauses. A scheduler sweep cancels sessions left paused past `GRAPH_WORKFLOW_DEBUG_MAX_PAUSE` (default 1 h)

### Changed
- `workflow_runs` gained a `debug_json` column (step-debug state: breakpoints, pending node; idempotent migration); `GraphRun` exposes a read-only `debug` object on paused runs
- New run status `paused` (chip in the Runs view); `GRAPH_RUN_STATUSES` and the `.env.example` updated
- 13 new backend tests (`tests/test_phase40.py`); docs + i18n updated in 5 languages

---

## [3.2.0] — 2026-07-20

### Added — Phase 39 (roadmap fase 7): Operations and governance
- **Retry from the failed node** (7.1) — `POST /v1/graph-workflows/runs/{id}/retry` relaunches a **failed** run as a new run over the origin's exact graph snapshot, seeded with the checkpointed node outputs: only the failed node and its downstream subgraph re-execute (the fase 2.4 crash-resume mechanics, on explicit request). The derived run records `origin_run_id` (replay does too, now); the Runs page shows a **↺ Retry** button on failed runs and the lineage in the run detail
- **Per-workflow environments** (7.2) — named environments on the workflow (`environments` map: `{name: {vars, secrets, version}}`): `vars` overlay the workflow `$vars` for runs in that environment, `secrets` remap `$secrets.<alias>` to another stored secret, and `version` pins a promoted graph version. `POST /{id}/environments/{env}/promote` pins the current (or a given) version — "promote to prod" while the editor keeps working on the current graph. Runs record their `environment` (new column, badge in the Runs view); selectable on manual runs (`environment` in the run body and in the editor's run panel) and on schedule/webhook triggers (`environment` in the trigger config). Environments travel with export/import (vars and secret **aliases** only — never values)
- **Per-workflow audit trail** (7.3) — `GET /{id}/audit` lists the workflow's audit entries (create/update/activate/deactivate/run/replay/retry/export/import/approval decisions/environment promotions), newest first; activate/deactivate are now audited too
- **Workspace share roles** (7.3) — sharing a workflow into a workspace now carries a **role**: `viewer` (inspect/import, the previous behaviour), `editor` (may also launch runs via `POST /{ws}/workflows/{wid}/run` — the run executes under the owner's profile), `approver` (may also decide the workflow's `human.approval` requests via the standard decision endpoint). Re-sharing updates the role in place
- **Per-node health metrics** (7.4) — `GET /{id}/stats/nodes`: per-node aggregates over the run history (executions by outcome, error rate, avg/p50/p95 duration, LLM tokens, last execution), unhealthiest first. New **Health** tab in the workflow shell rendering the table plus the audit trail
- **Approval via Telegram** (7.5) — `human.approval` notifications with `telegram: true` now carry inline **✅ Approve / ❌ Reject** buttons; the bot callback verifies the chat is linked to the owning profile and settles the request exactly like `POST /approvals/{id}/decision` (first writer wins), so the suspended run resumes within seconds

### Changed
- `workflow_runs` gained `environment` and `origin_run_id` columns; `workflows` gained `environments_json`; `workspace_workflows` gained `role` (all with idempotent migrations)
- `notification_service.notify_telegram` accepts an optional inline-keyboard `buttons` parameter
- **Phase 5.2 (export/import/sharing) extended for fase 7.2/7.3**: `GET /{id}/export`, `POST /import` and `POST /{ws}/workflows/{wid}/import` now carry the workflow's `environments` (vars overlays + secret **aliases** only, never values — same portability rule as `$secrets`); `POST /{ws}/workflows` accepts an optional `role` (fase 7.3) alongside `workflow_id`
- **Phase 5.1 (metrics) extended for fase 7.2**: `GET /v1/graph-workflows/stats` accepts an optional `?environment=<name>` query param scoping every aggregate (runs, success rate, avg duration, tokens) to runs executed in that named environment; workflows with zero matching runs still appear (`runs: 0`) rather than being omitted
- 14 new backend tests (`tests/test_phase39.py`); docs + i18n updated in 5 languages

---

## [3.1.0] — 2026-07-19

### Added — Phase 38 (roadmap fase 6): Engine extension — triggers, loops, composition
- **`success` trigger** (6.1) — a workflow fires on the **successful** completion of another one (or any, with the same `workflow_id` filter and anti-loop guards as the `error` trigger); `$trigger = {workflow_id, workflow_name, run_id, output}` where `output` is the completed run's sink output. Enables "A then B" pipelines without subworkflows. Attachable from the schedules page and the run panel
- **Multiple cron expressions per schedule** (6.1) — the `cron` pattern accepts a `crons` list (UI: one expression per line) encoded as a `crons:<e1>|<e2>` recurrence; the next fire is the earliest across all expressions — mixed timetables without duplicating the workflow
- **`file.watch` trigger** (6.2) — poll-based watch (reuses the schedule loop, no inotify) over a subfolder of `GRAPH_WORKFLOW_FILES_DIR` with a glob pattern; fires per created/modified file with `$trigger = {path, event, size}`; the first poll only seeds the snapshot. Per-trigger `interval`, floored by `GRAPH_WORKFLOW_WATCH_POLL_SECONDS` (default 60 s)
- **`email.inbound` trigger** (6.2) — IMAP poll (credentials via `$secrets`, `password_secret` in the trigger config) with sender/subject filters; `$trigger = {from, subject, body, attachments}` with attachments saved to the workspace storage (readable with `file.read`)
- **`while` loop node** (6.3) — condition re-evaluated before every iteration (`$item` = previous body output, `$index` = iteration), with a **mandatory iteration cap** (`maxIterations`, default 100, hard limit `GRAPH_WORKFLOW_WHILE_MAX_ITERATIONS`, default 1000); continues on `done` with `{items, count, capped}` — async-API polling and pagination without subworkflow recursion
- **Sub-workflow contracts** (6.4) — optional `input_schema` / `output_schema` JSON Schemas on the workflow (new columns, **Contracts** section in the run panel, portable in export/import): the `subworkflow` node validates the payload before the child run and the sink output on return (dependency-free subset validator: type/required/properties/items/enum). Workflows with an input contract appear in the palette as typed **`workflow.<id>`** nodes whose params mirror the contract's properties; the LLM generator's catalog context includes them, so it can compose existing workflows
- **`kb.search` node** (6.5) — semantic search over the Phase 28 knowledge base from inside a workflow: `query` (expression, defaults to node input), `top_k`, optional `document_ids` filter; output `{results: [{text, score, source, chunk_index}], count}` — RAG in workflows without a generic `llm.agent`
- **Per-host rate limiting** (6.6) — `http.request` (and `notify.webhook`, which routes through it) is throttled per host via a process-wide sliding one-minute window: node-level `maxRequestsPerMinute` and/or the global `GRAPH_WORKFLOW_RATE_LIMITS` map (`host=rpm` pairs or JSON); over-cap requests **wait** (never fail) and the wait is reported as `rate_limited_s` in the node output

### Changed
- `TRIGGER_TYPES` extended to `success` / `file.watch` / `email.inbound` (schema, engine, catalog, schedules UI)
- Workflow export snapshot now carries `input_schema` / `output_schema`; `POST /import` restores them
- 21 new backend tests (`tests/test_phase38.py`); docs + i18n updated in 5 languages

---

## [3.0.0] — 2026-07-17

### Added — Phase 30: Workflow runs & schedules pages, engine hardening
- **Runs page** — cross-workflow `/graph-workflows/runs` view with status/workflow filtering, "run now" launcher, and a detailed run view (per-node status, input/output, timing)
- **Schedules page** — cross-workflow `/graph-workflows/schedules` overview listing every `schedule`/`webhook`/`event` trigger across all workflows, with create, delete, and enable/disable toggling from one place
- **Parallel branch execution** — independent ready nodes in a run wave execute concurrently via `asyncio.gather`, bounded by a `GRAPH_WORKFLOW_MAX_CONCURRENT_NODES` semaphore (default 8); `merge` nodes synchronize join points
- **Run overlay on canvas** — the editor canvas colours nodes live by run status (SSE + poll) and can re-attach to runs started elsewhere
- **Canvas editing** — copy/paste, duplicate, undo/redo on the graph canvas

### Added — Phase 31–32 (roadmap fase 1): Editor refactor, workflow shell, variables & secrets
- **Componentized editor** — `graph-workflow-page.component` split into six standalone components (`graph-canvas`, `node-palette`, `editor-toolbar`, `node-inspector`, `edge-inspector`, `run-panel`) under `frontend/src/app/features/workflows/editor/`; the page component is now a thin orchestrator
- **Workflow shell** — `/graph-workflows/:id` route with **Editor | Runs | Schedules** tabs scoped to a single workflow, alongside the existing global Runs/Schedules pages
- **`$vars` and `$secrets`** — per-workflow variables (`variables_json` column, `PATCH variables`, editable from the run panel) and Fernet-encrypted, profile-scoped secrets (`workflow_secrets` table, `GET/PUT/DELETE /secrets`), referenced as `$secrets.<name>`; never returned in cleartext, masked in previews, excluded from export
- **Versioning UI** — a **Versions** section in the run panel lists immutable version snapshots with restore, on top of the existing backend snapshot-on-save/restore

### Added — Phase 33 (roadmap fase 2): Engine reliability
- **Backoff strategy** — `backoffStrategy` (fixed | exponential, capped at 60 s) alongside existing `retry`/`backoff`/`timeoutMs`, with inspector fields and catalog-driven defaults on drop (`http.request`: 2 exponential retries + 60 s timeout; `llm.*`: 1 retry + 120–300 s timeout)
- **Concurrency queue** — `max_concurrent_runs` per workflow (0 = unlimited, **Execution** section in the run panel); runs beyond the limit start `queued` and are promoted FIFO by `_maybe_start_queued()` at run completion and on startup
- **Checkpoint & resume** — per-wave checkpoints now include each node's active output handles; `resume_interrupted_runs()` (flag `GRAPH_WORKFLOW_RESUME_ON_STARTUP`) resumes `running`/`pending` runs from checkpoint on startup, re-executing only the missing subgraph and closing orphaned node runs as "interrupted by restart"
- **Error trigger** — a new `error` trigger (+ catalog node) fires when another run fails, with `$trigger = {workflow_id, workflow_name, run_id, error, failed_node}`, workflow filter, and anti-loop guards; curated example `error-alert-hub`

### Added — Phase 34 (roadmap fase 3): Editor developer experience
- **Single-node test** — `POST /{id}/nodes/{node_id}/test` runs one node in isolation (current or unsaved params, optional mock input) with no run recorded; result shown inline in the inspector and projected onto the canvas
- **Pinned output** — `pinnedOutput` on `GraphNode` (saved, versioned, exported) lets node tests and expression previews resolve `$node.<id>.output` from a frozen pin instead of run history; production runs ignore pins
- **Inspector run history** — a **Last run** section on the selected node (status/output/error)
- **Multi-selection** — shift-click / `Ctrl+A` selection, group drag, copy/paste of a selection with internal edges remapped, `Del`/`Backspace`
- **Pan/zoom, minimap, auto-layout** — background-drag pan, cursor-anchored zoom, a clickable/draggable minimap with viewport (double-click to fit), longest-path auto-layout ("Reorder", undoable), and a "fit view" toolbar action
- **Template gallery** — the examples panel now renders a mini-SVG preview of each example's graph with category filtering

### Added — Phase 35 (roadmap fase 4): New node kinds
- **`llm.classify` / `llm.extract`** — structured-output nodes: classify into a fixed category set (`{category, confidence}`, retryable on out-of-list results) and extract fields per a JSON Schema (`{data}`, tolerant of surrounding prose/code fences); share the model picker, failover chain, and cache from `llm.completion`
- **`db.query`** — parameterized queries against SQLite (workspace storage) or Postgres (via `$secrets` DSN, optional `asyncpg`), output `{rows, count, rowcount}`, capped at 1000 rows
- **`file.read` / `file.write` / `file.parse`** — auto/json/csv/lines formats, 10 MB cap, all paths sandboxed under `GRAPH_WORKFLOW_FILES_DIR` (traversal and absolute paths rejected)
- **`human.approval`** — suspends the run in a new `waiting` status, creates a `workflow_approvals` row, notifies in-app (+ optional Telegram), and waits for a decision or timeout (`onTimeout: reject|fail`, capped by `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT`, default 7 days); `approved`/`rejected` output handles; survives restarts via the Phase 33 resume path; `GET /approvals` + `POST /approvals/{id}/decision`, approve/reject UI on the runs page. Curated examples `approval-gate-deploy`, `ticket-triage-classify`

### Added — Phase 36 (roadmap fase 5): Platform features
- **Stats** — `GET /v1/graph-workflows/stats`: per-workflow run outcomes, success rate, average duration, and summed LLM token usage; a dashboard strip on the Runs view plus a per-run token total
- **Export/import & sharing** — export now includes referenced `$secrets` names (never values); `POST /import` validates schema/node limits and surfaces non-blocking warnings (unknown node types, broken edges, missing secrets); workflows can be shared into a workspace (`workspace_workflows` table, `GET/POST /{ws}/workflows`, `DELETE /{ws}/workflows/{wid}`, `POST /{ws}/workflows/{wid}/import`)
- **LLM-generated workflows** — `POST /generate` (`{prompt, model?, failover_chain?}`) uses the node catalog as LLM context and returns a validated, auto-laid-out unsaved draft graph; `POST /generate/stream` streams SSE progress logs (catalog → call → response → validation → layout → done/error); editor dialog with model picker + failover chain
- **Editor UX** — template gallery as a centered modal with richer cards; collapsible workflow list (persisted preference)

### Changed
- `app_version` default bumped from `2.2.0` to `3.0.0` (`backend/app/core/config.py`); `frontend/package.json` bumped to match

---

## [2.2.0] — 2026-07-09

### Added — Phase 29: Visual node-graph workflow engine (n8n-style)
- **DAG engine + expression resolver (29.a)** — a deterministic **topological scheduler** (`workflow_graph_service.py`) executes a graph of typed nodes: each ready node resolves its params, runs, persists a `workflow_node_run`, checkpoints the run context and activates its output handles; independent ready nodes run in parallel via `asyncio.gather`, nodes with no live input are `skipped`, and per-node `retry`/`backoff`/`continueOnFail` bound failures. Runs are durable and stream live over SSE. New tables `workflows`, `workflow_versions`, `workflow_runs`, `workflow_node_runs`, `workflow_triggers` (same SQLite). Coexists with the Phase 18 agent runs — the agent loop becomes the `llm.agent` node
- **Safe expression resolver (29.a)** — a standalone, unit-tested `expression_resolver.py` resolves `={{ … }}` expressions by **walking a Python AST over a whitelist (no `eval`/`exec`)**: path navigation (`$node.<id>.output.<path>`, `$json`, `$trigger`, `$env`, `$now`), whitelisted functions (`default`/`upper`/`lower`/`len`/`join`/`slice`/`first`/`last`/`get`/…), operators/comparisons/ternary, native-type passthrough and string interpolation, plus a `=py:` escape hatch into the `python_exec` sandbox for real logic
- **Node kinds** — `manual`/`schedule`/`webhook`/`event` triggers, `tool.<name>` (a generic wrapper over **any** registry tool — built-in/MCP/custom, zero new code per tool), `set`, `if`, `switch`, `merge`, `filter`, `code` (sandbox), `llm.completion` and `llm.agent` (runs the Phase 18 agent loop). `GET /v1/graph-workflows/node-types` exposes the full palette catalog
- **Triggers (29.b)** — `schedule` (cron/RRULE/NL via `reminder_parsing`, fired from a `reminder_service`-style poll loop with `next_run_at` recompute — absorbs Phase 27), public token-scoped `webhook` (`POST /v1/wf/hooks/{token}`, body → `$trigger`), and internal `event` dispatch; enable/pause/delete + "run now"
- **Visual canvas (29.c)** — an Angular editor on the new **`/graph-workflows`** page: a dependency-free **SVG canvas** with draggable nodes, bézier edges and click-to-connect handles, a categorised **node palette**, a schema-driven per-node **inspector**, and a **run & triggers panel** that colours nodes live from the SSE stream. Five-locale labels (en/it/es/fr/de)
- **Versioning (29.d)** — every graph save snapshots an immutable `workflow_versions` row; `GET /{id}/versions` + `POST /{id}/versions/{v}/restore` roll back
- **Examples gallery** — four curated, one-click-importable graph workflows (`GET /v1/graph-workflows/examples`): RSS morning digest, weather-aware greeting, webhook → knowledge-base answer, and a branching page keyword watcher. Import creates a new workflow from the example graph and opens it on the canvas. Documented in [`docs/examples/graph-workflows.md`](docs/examples/graph-workflows.md); a CI guard asserts every node type/tool an example uses still exists
- **MCP & custom tools in flows** — the `/node-types` palette is now discovered per profile: every configured **MCP server tool** (`tool.mcp__*`) and the profile's **custom HTTP tools** (`tool.custom__*`) appear as drag-in nodes (new **MCP & custom** palette group) and run natively via the existing `tool.<name>` executor. The `llm.agent` node is handed the full tool set (built-in + MCP + custom), matching the Phase 18 agent
- **Model picker in AI nodes** — `llm.completion` / `llm.agent` model params render a reusable `ModelPickerComponent` with the **same catalog and filters as the chat page** (provider / capability / free-only, name search, and models hidden on `/providers`), reading the shared `UserPreferencesService`; it expands inline in the inspector
- **Loop constructs** — `for` (for-each over an array, `$item`/`$index` in scope) and `repeat` (N times) control nodes with `loop`/`done` outputs: the engine runs the body subgraph once per iteration, collects each result, and continues on `done` with `{items, count}` (iterations capped)
- **Palette UX** — the node palette's category sections are collapsible, and the **MCP & custom** group has two collapse levels (MCP server → its tools). The AI-node model picker expands inline instead of as a floating popup
- **REST** — `GET/POST/PATCH/DELETE /v1/graph-workflows` (CRUD + auto-versioning, audited), `POST /{id}/run`, `POST /{id}/activate|deactivate`, `GET /{id}/runs`, `GET /runs/{rid}` (+ node_runs), `GET /runs/{rid}/stream` (SSE), trigger CRUD. Settings `GRAPH_WORKFLOW_SCHEDULER_ENABLED`, `GRAPH_WORKFLOW_MAX_NODES`. Backend covered by `tests/test_phase29.py` (resolver unit tests + end-to-end engine/trigger tests)

---

## [2.1.0] — 2026-07-08

### Added — Phase 26: Semantic response cache (extends 19.c)
- **Semantic cache** — when `SEMANTIC_CACHE_ENABLED`, on an exact-match miss `cache_service` embeds the normalized last user message and compares it (cosine) against stored embeddings of recent entries in the same `(model, temperature, max_tokens)` bucket; a hit above `SEMANTIC_CACHE_THRESHOLD` replays the saved reply flagged `cached_semantic` (⚡~ chip). Same 19.c exclusions (tools, `agent/*`, multimodal); degrades silently to exact-match-only when no embedding provider is reachable. Settings `SEMANTIC_CACHE_ENABLED`/`_THRESHOLD`/`_MAX_ENTRIES`; `cache_service.stats()` (in `/info`) reports semantic vs exact hits

---

## [2.0.9] — 2026-07-08

### Added — Phase 24: Working examples & cookbook
- **Example workflows & custom tools** — curated, one-click-importable Phase 18 workflow definitions (Examples gallery on `/workflows`: morning news digest, website watcher, KB research report, weather-aware reminder) and custom-tool definitions using keyless public APIs (currency, Wikipedia, public holidays, geocoding + a bearer-auth template on `/tools`), each verified end-to-end by CI smoke tests. Documented in `docs/examples/workflows.md` and `docs/examples/custom-tools.md`

---

## [2.0.8] — 2026-07-07

### Added — Phase 23.5: Local stdio MCP servers (self-hosted runtimes)
- **Bundled runtimes + guardrails** — the backend image can bundle optional **Node.js** (`npx`) and **uv** (`uvx`) layers (`--build-arg INSTALL_NODE`/`INSTALL_UV`) so pasted `mcpServers` stdio entries work beyond the `docker run` (DooD) path; the `app` user gets a real writable `$HOME`. `GET /v1/mcp/runtimes` reports which launchers are on `PATH` (chips on `/mcp`); `_open_stdio` preflights with `shutil.which` and enforces `MCP_STDIO_ENABLED` + an `MCP_ALLOWED_COMMANDS` allowlist; `POST /v1/mcp/deployment-check` computes what a pasted bundle needs per server. Documented in `docs/mcp-deployment.md`

---

## [2.0.7] — 2026-07-07

### Added — Phase 28: wikillm enhanced knowledge base (MarkItDown + KG + sqlite-vec)
- **Structure-aware ingestion (28.a)** — `document_converter.py` wraps Microsoft **MarkItDown**, converting every upload (PDF, DOCX, PPTX, XLSX, CSV, HTML, EPUB, JSON, XML, TXT/MD) and fetched URLs to canonical **Markdown** (`kb_documents.markdown`), replacing the old `PyPDF2`/`python-docx`/regex-HTML extraction. Chunking happens *within* heading sections (`chunk_markdown_with_offsets`), tagging each chunk with a `section_path`/`heading` breadcrumb and char offsets for citation deep-linking
- **sqlite-vec ANN store (28.b)** — chunk vectors are mirrored into a `vec0` virtual table (`kb_chunk_vec`, cosine) loaded as a SQLite extension, so retrieval's vector arm is an ANN KNN (`knn_chunks`) instead of an O(n) numpy scan; degrades gracefully to the numpy fallback when the extension is unavailable (`RAG_USE_SQLITE_VEC`). Still one SQLite file
- **Wiki + knowledge graph (28.c)** — `wiki_service.py` builds a per-document section tree (`kb_wiki_pages`); `graph_service.py` (LLM-free) extracts a deduped entity graph (`kb_graph_nodes`/`kb_graph_edges` + `kb_chunk_entities`), with optional 1-hop expansion at retrieval (`RAG_GRAPH_EXPAND`). New `GET /documents/{id}/wiki`, `GET /graph`, `POST /reingest`; web Wiki/Graph inspectors + a profile-wide force-directed graph view
- **GraphRAG (28.d)** — optional LLM entity/relationship extraction, dependency-free label-propagation community detection + community summaries, and map-reduce **global search** on the *same* tables (no schema change). New `graphrag_service.py`; `GET /graph/status`, `GET /graph/communities`, `POST /graph/communities/rebuild`, `POST /graph/global-search`; a "GraphRAG" panel on the Knowledge page. Every LLM call is best-effort and cost-bounded

---

## [2.0.6] — 2026-07-06

### Added — Phase 23.d: Extended cross-channel reminders
- **Extended reminders (23.d)** — the Phase 14 Telegram-only `telegram_reminders` table is replaced by a channel-agnostic `reminders` table (auto-migrated) + shared `reminder_parsing.py`: relative (`+30m`/`2h`/`1d`), absolute, **recurrence** (`every day HH:MM`, `every <weekday>`, `cron:…`) and IT/EN **natural-language** phrasings, with an LLM parse fallback. Firing moved to a channel-agnostic ~20s polling loop in `reminder_service.py` (fires whether or not the bot is connected). New `/remindai` creates **smart reminders** (a bounded tool loop generates the content at fire time). Fired Telegram reminders carry a 💤/🔁/🗑 inline keyboard. REST `GET/POST/PATCH/DELETE /v1/reminders` (+ `snooze`/`repeat`) backs a web Reminders panel with per-reminder delivery channel (`telegram`/`web`/`both`) and timezone override

---

## [2.0.5] — 2026-07-06

### Added — Phase 23.c: Cross-channel notifications (UI ↔ Telegram)
- **Cross-channel notifications (23.c)** — `notification_service.py` bridges events between channels for linked users: web→Telegram push on workflow/image/long-reply completion (forwarded via `POST /v1/notifications/trigger`), and web toast/badge on Telegram events (reminder fired, `/kb` ingest), persisted in `notification_events` and streamed live over `GET /v1/notifications/stream` (fetch-based SSE). Per-event-type opt-in matrix in a "Notifications" sidebar panel (`NotificationPrefsService`, roaming via the `preferences` blob); a per-chat `/notify on|off` mutes the web→Telegram direction

---

## [2.0.4] — 2026-07-05

### Added — Phase 23.a/b: Telegram ↔ web convergence
- **Shared conversation history across Telegram and web (23.a)** — for a **linked profile** (`/link`), Telegram exchanges are now persisted as regular profile conversations instead of the in-memory per-chat buffer. A per-chat *active conversation* is tracked in `telegram_prefs.active_conversation_id` (warm-cached at boot); each successful turn (text/voice/photo/document) is appended via `conversation_repository.append_messages`, creating the conversation lazily on the first message with an auto-generated title (`title_service`). `/history` now lists the profile's recent conversations across **both** channels with an inline keyboard to resume any of them (`resume:<id>` callback — rehydrates the full context, even across a bot restart); `/new` and every model/mode switch detach the active conversation so the next message starts a fresh one. Telegram-started conversations surface in the **web sidebar with an ✈️ badge** (new `conversations.channel` column + `ConversationSummary.channel`). Quick-action refinements stay in-memory only; unlinked chats keep the legacy in-memory session. Five-locale bot strings (`history_*`) + a web catalog label (`chat.conversations.viaTelegram`) added
- **MCP tools from Telegram (23.b)** — the Telegram bot can now run the **full tool loop**. When tools are enabled for a chat, the built-in tools, the linked profile's custom tools and every discovered `mcp__<server>__<tool>` are merged into the completion request and executed through the **shared** `ChatService._stream_with_tools`, so tool behavior is identical to the web chat. New `/tools` command lists the available tools grouped by kind (🧩 built-in / 🔌 MCP / 🛠 custom) with an inline ON/OFF button; `/tools on|off` flips it directly. The toggle is persisted in a new `telegram_prefs.tools` column (migration) and warm-cached at boot (OFF by default). Tool-call progress is shown live in the streaming reply (⚙ tool name → ✅ on result). Agent mode (`agent/*`) is left to orchestrate its own tools. MCP discovery is cached in `mcp_service` and only re-probed on `/tools` listing / cold cache. Five-locale bot strings + a `/tools` command-menu entry added

### Changed
- `conversations` gains a `channel` column (default `'web'`; migration + `ConversationSummary` schema) and `telegram_prefs` gains `active_conversation_id`, for cross-channel Telegram history (23.a)
- `telegram_prefs` gains a `tools` column (default `0`; migration) backing the per-chat `/tools` toggle (23.b)

---

## [2.0.3] — 2026-07-05

### Added — Phase 22: Internationalization (i18n)
- **Web UI multi-language (22.a)** — dependency-free runtime i18n layer under `frontend/src/app/core/i18n/`: `Locale` metadata (en/fr/de/it/es with native labels + BCP-47 tags), one flat catalog per locale (**560 keys**), an `I18nService` (active-locale signal, first-visit browser-language auto-detection, `translate()` with `{placeholder}` interpolation and `active → default(it) → key` fallback), and an impure `TranslatePipe` (`| t`) so switching language re-renders instantly without a reload. A 🌐 language switcher in the navbar; the choice is persisted in `localStorage` **and** per profile via the new `PATCH /api/v1/profiles/{id}` (`locale`), adopted on profile select/restore. **Full UI coverage** — every surface is localized: navbar/menus + tooltips, the entire chat page & sidebar (labels, actions, toasts, notifications, slash commands), login, and all feature pages (Providers, Discovery, Compare, Stats, Tools, Workflows, MCP, Workspaces + threaded comments, Templates, Tags, Knowledge, Memory, Ops, Info, Help, profile modal, shared view). TTS and voice input now follow the active locale's BCP-47 tag (previously hardcoded `it-IT`)
- **Telegram fr/de/es (22.b)** — `app/telegram/i18n.py` `MESSAGES` + `SUPPORTED_LOCALES` extended with French, German and Spanish for all commands, inline keyboards, reminders and error messages; the `/lang` keyboard auto-renders all 5 locales
- **Locale-aware formatting (22.c)** — `localeNumber` / `localeCost` / `localeDate` pipes + `I18nService` formatters over the `Intl` API (wired into stats costs and the chat telemetry footer); Telegram reminder confirmations use a locale-aware date order
- **Docs (22.d)** — new `docs/en/internationalization.md` + `docs/it/internazionalizzazione.md`, linked from both README indexes
- **Tests / CI (22.e)** — `backend/tests/test_i18n.py` (Telegram 5-locale key parity, formattability, fallback chain, profile-locale endpoint) + a runnable web catalog check (`frontend/scripts/check-i18n.mjs`, `npm run i18n:check`); both wired into a new `.github/workflows/ci.yml`
- **Login page localized** — the login card (subtitle, field labels, placeholder, button, error messages) now uses the i18n catalog (`auth.*` keys, all 5 locales)
- **Per-language documentation & screenshots** — docs restructured to `docs/en/` + `docs/it/` (renamed from `features/`/`funzionalita/`) plus new `docs/fr/`, `docs/de/`, `docs/es/`, **each fully translated** (all 15 feature pages + README index + i18n page per language); each language ships its own `screenshots/`. `copy-docs.mjs` now publishes all 5 languages with per-language screenshots; the `/help` page loads the doc set for the active UI language (English fallback). New `frontend/scripts/screenshots.mjs` (Playwright) captures each page per language against a running instance
- **Roaming preferences** — user and profile settings (theme/accent, notification opt-ins, and other UI preferences) are persisted server-side in a `preferences` blob and roam across devices/sessions rather than living only in `localStorage`

### Fixed
- **Help page now follows a live language switch** — the `/help` page fixed its doc language once at construction, so switching the UI language left the currently-shown guide in the old language until a reload. It now reacts to the active locale (via an `effect`) and reloads the manifest + current doc on change. All per-language screenshots regenerated against the fully-localized build

### Changed
- `profiles` gains a nullable `locale` column (migration + `Profile` schema); `PATCH /api/v1/profiles/{id}` validates against the 5 supported locales
- The shared `docs/screenshots/` folder was removed in favour of per-language `docs/<lang>/screenshots/`; doc image references updated accordingly

---

## [2.0.2] — 2026-07-04

### Added — Telegram knowledge base (RAG)
- **`/kb` and `/rag` in the Telegram bot** — the web profile's knowledge base is extended to the Telegram channel (requires a linked profile via `/link`): send a PDF/TXT/DOCX/MD file with a `/kb` caption to ingest it through the same `rag_service.ingest` pipeline (with sha256 duplicate detection); `/kb list`/`/kb del <id>` manage documents; `/rag on|off` toggles knowledge-base injection per chat (persisted in `telegram_prefs.rag`, OFF by default), folding retrieved chunks into the reply with a 📚 sources footer

---

## [2.0.1] — 2026-07-04

- Re-tag of [2.0.0] (release/CI fixup); no code changes.

---

## [2.0.0] — 2026-07-04

### Changed — Web UI 2.0: navigation & sidebar overhaul
- **Hierarchical navbar** — the flat 12-item navbar became macro-menus with click-to-open submenus: **Chat**, **Modelli** (Providers, Discovery, Compare, Stats), **Tools** (Tools, Workflow, MCP, Workspace), **Risorse** (Template, Tag, Knowledge, Memoria), **Info** (Guida, Info, Ops). Outside-click close, admin-only items hidden when not admin, empty groups hidden, accordion behaviour on mobile
- **Lighter chat sidebar** — now keeps only the per-chat runtime controls (**Modello**, **Sistema**, **Parametri**) plus the **ON/OFF switches** (Tool calling, Knowledge/RAG, Memoria) each with a "Gestisci →" link to its page. The Conversations list became a **picker overlay** (button + `Ctrl+K`) with search, tag filtering, selection and deletion
- **Management panels promoted to pages** — Template → `/templates`, Tag → `/tags`, Knowledge base → `/knowledge`, Memoria → `/memory` (new routed standalone components reusing the existing `TemplateService` / `TagService` / `KnowledgeService` / `MemoryService`); the sidebar Provider and Tool-list panels were consolidated into the existing `/providers` and `/tools` pages

### Added
- **Provider visibility filter** — in the sidebar **Modello** section, a compact chip filter picks which providers' models appear in the model picker (persisted `selectedProviders`, feeds `filteredModels`)
- **Per-model visibility curation** — on the **Providers** page each provider's model list has a per-model show/hide eye toggle plus per-provider **Mostra tutti / Nascondi tutti**, a visible/hidden counter and an always-visible "N nascosti" badge on the card. Hidden models are excluded from the chat model picker (persisted `hiddenModels`) — fixes the endless scroll on providers with many models
- **Available tools grouped by MCP server** — the `/tools` page now lists every tool exposed to the model, grouped into a card per MCP server (plus Built-in / Custom), each showing the tool names and descriptions

### Removed
- The Provider / Templates / Tags / Knowledge-list / Memory-list panels were removed from the chat sidebar (moved to dedicated pages); dead component state, methods and preferences were cleaned up (`UserPreferences.sectionsOpen` reduced to `model` / `system` / `params`; new `hiddenModels` preference added)

---

## [1.9.4] — 2026-07-04

### Added — Phase 20: Collaboration
- **Shared workspaces (20.a)** — team-scoped workspaces (`workspaces` + `workspace_members`) owned by a user, with role-based access (`owner` > `admin` > `editor` > `viewer`). Members are invited by email; conversations and knowledge-base documents (owned by an individual profile) are *shared into* a workspace via join tables (`workspace_conversations` / `workspace_documents`), making them visible to every member. `GET/POST/PATCH/DELETE /v1/workspaces` + `/{ws}/members`, `/{ws}/conversations`, `/{ws}/documents` — sharing requires editor+ and ownership of the resource, membership management requires admin+, deletion is owner-only, any member may self-leave. Web UI: a "Workspace" page with a workspace list/create sidebar and a detail pane for members and shared conversations/documents
- **Annotations & comments (20.b)** — threaded comments on shared conversations (`comments` table, `parent_id` threading, `message_id` per-message anchoring, soft-deleted so replies keep their anchor). Access mirrors conversation reach — the owner or any member of a workspace it is shared into can read/post; editing/deleting is restricted to the comment's author. `GET/POST/PATCH/DELETE /v1/conversations/{id}/comments`. Web UI: a collapsible threaded comment panel under each shared conversation

---

## [1.9.0] — 2026-07-03

### Added — Phase 19: Personalization & quality
- **Per-profile persistent memory (19.a)** — new `profile_memories` table + `/v1/memories` CRUD endpoints (list/add/edit/toggle/delete, forget-all, per-profile switch). After each persisted exchange an async low-cost LLM call (`MEMORY_EXTRACTION_MODEL`, default = `DEFAULT_MODEL`) extracts `add`/`update`/`delete` operations (dedup + `MEMORY_MAX_ITEMS` cap); enabled memories are compacted into a `<user_memory>` block appended to the system prompt (`MEMORY_MAX_CHARS` budget). Three-level toggle: per-profile `profiles.memory_enabled`, per-request `memory:false` (incognito), per-memory `enabled`. SSE `memory_context` frame → 🧠 chip on memory-grounded replies. Web UI: "Memoria" sidebar panel (list/add/toggle/delete/forget-all + auto-extraction switch + incognito ON/OFF). Telegram: `/memory on|off|list|del <id>` (per-chat toggle persisted in `telegram_prefs`, memories via the linked profile), memory injected/extracted in `_stream_reply`
- **LLM auto-titling (19.b)** — after the first persisted exchange a background task (`TITLE_MODEL`, opt-out `AUTO_TITLE_ENABLED=false`) generates a concise conversation title, replacing the first-60-chars heuristic; the sidebar list refreshes to pick it up
- **Response cache (19.c)** — exact-match in-memory LRU cache of completed replies (`RESPONSE_CACHE_ENABLED`, `RESPONSE_CACHE_TTL_SECONDS`=600, `RESPONSE_CACHE_MAX_ENTRIES`=256) keyed on model/messages/temperature/max_tokens; hits skip the provider entirely and are replayed as a single chunk flagged `cached` (⚡ chip in the UI). Requests with tools, `agent/*` models or multimodal content are never cached
- **Feedback & evaluation (19.d)** — 👍/👎 (+ optional note) on persisted assistant messages: `rating`/`feedback_note` columns, `PUT`/`DELETE /v1/feedback/messages/{id}`, `GET /v1/feedback/stats`, `GET /v1/feedback/export` (dataset pairing each rated reply with its prompt); hover thumbs in the web UI; lightweight regression harness `backend/scripts/eval_regression.py` re-runs 👍-rated prompts and flags similarity regressions
- **Built-in tools expansion (19.e)** — 8 new registry tools: `kb_search` (agentic RAG via `rag_service.retrieve`), `search_conversations` (FTS5 episodic memory), `generate_image` (image chain as a tool; the model gets a placeholder, the user gets the image), `get_weather` (Open-Meteo, keyless), `fetch_rss` (RSS 2.0/Atom), `create_reminder` (Telegram reminders via the linked profile, live-scheduled on the running JobQueue), `extract_document` (PDF/DOCX/TXT/MD from URL without KB ingestion), `http_request` (generic GET/POST with SSRF hardening + optional `HTTP_REQUEST_ALLOWED_DOMAINS` allowlist). `kb_search`/`search_conversations`/`create_reminder` receive the caller's profile automatically

- **Info page** — new `/info` page in the web UI (navbar entry) showing the web UI version (from `package.json` at build time), backend metadata from the new `GET /v1/info` endpoint (name, version, environment, Python/platform, uptime, default model, timezone, DB path/size, configured providers, response-cache stats, feature flags), the API endpoints in use (base URL, health/ready/metrics, OpenAPI docs link) and live health/readiness status
- **Version stamping** — release version is now a single source of truth: the Makefile passes the git tag as `--build-arg APP_VERSION` to every image build; the backend exposes it via the `APP_VERSION` setting (FastAPI docs + `GET /v1/info`, fallback `1.9.0`) and the frontend's `package.json` is stamped before `ng build` so the Info page always matches the build tag
- **Unified provider model discovery** — the eight per-provider discovery endpoints (`*_discovery.py`) were replaced by a single `model_discovery` service + `discovery_refresh` background loop (`DISCOVERY_REFRESH_ENABLED`, every `DISCOVERY_REFRESH_HOURS`); the static `provider_models.yaml` catalogs were removed in favour of the live discovered catalog; the Discovery page was reworked accordingly
- **Feature documentation** — new "Memoria e personalizzazione" / "Memory & personalization" pages in `docs/it/` and `docs/en/` (memory, auto-titling, cache, feedback, Info page) and the built-in tools tables updated with the 8 new Phase 19 tools

### Security
- **SSRF hardening** — `read_url`, `fetch_rss`, `extract_document` and `http_request` now refuse URLs whose host resolves to private/loopback/link-local/reserved addresses (`assert_public_url`)

---

## [1.8.0] — 2026-07-02

### Changed
- **Code structure refactor** — cleanup pass across backend and frontend for readability and maintainability (no functional changes)

---

## [1.7.0] — 2026-07-01

### Added
- **Chat loading indicators** — animated progress bar below the topbar showing the request phase: model warm-up (amber), tool execution (blue), streaming (standard); pending tool-call bubbles show a spinner until the result arrives
- **Model search & filtering** — text search over the model list in the sidebar, alongside the capability/availability filters
- **Tool grouping** — tools in the sidebar grouped by origin (built-in / custom / per-MCP-server) with collapsible groups

---

## [1.6.0] — 2026-06-30

### Added — Phase 18: MCP server management
- **MCP server registry** — configure MCP servers in the standard `mcpServers` JSON shape, persisted in a dedicated `mcp_servers` table (admin-managed, global). Two transports: **stdio** (`command`/`args`/`env`/`cwd`) and **sse** (`type: "sse"` + `url`/`headers`); the transport is inferred from `url` when `type` is omitted
  - New endpoints (admin-only, audited): `GET/POST /v1/mcp/servers`, `GET/PATCH/DELETE /v1/mcp/servers/{id}`, `POST /v1/mcp/servers/{id}/test`, `POST /v1/mcp/reload`, `GET /v1/mcp/config`, `POST /v1/mcp/import`
  - New `mcp_client` — minimal JSON-RPC 2.0 MCP client (no SDK dependency; Python 3.9-compatible) supporting both transports: **stdio** (spawn `command`/`args`, newline-delimited JSON-RPC over stdin/stdout) and **sse** (HTTP+SSE to a `url`, with `endpoint`-event POST-back); runs the `initialize` handshake, then `tools/list` / `tools/call`
  - New `mcp_service` — probes server health, caches tool discovery, and injects discovered tools into the chat tool-loop namespaced `mcp__<server>__<tool>` (merged into `GET /v1/tools`, routed by `execute_tool`)
  - New admin-only `/mcp` page — paste/import a standard bundle, enable/disable toggle, per-server health + discovered tools, test connectivity, export `mcp.json`
- **Docker-out-of-Docker for the backend** — the backend image ships the `docker` CLI and the compose service mounts the host daemon socket (`group_add` with the `docker` group GID), so MCP servers defined as `docker run …` launch as sibling containers

### Fixed
- **NVIDIA provider had no tool-calling support** — `nvidia_provider` never forwarded `tools`/`tool_choice` to the NIM API and dropped `tool_calls` from responses, so neither built-in nor MCP tools worked with any `nvidia/*` model. It now serializes `tool_calls`/`tool_call_id`/`name` on outgoing messages, forwards the tool definitions, and propagates returned `tool_calls` into the completion (verified: Nemotron now calls `mcp__wikillm__list_documents` for "quali documenti ho nella wiki?")
- **Streaming tool loop crash** — `ChatService._stream_with_tools` shadowed the module-level `metrics` with a local of the same name, raising `UnboundLocalError` on every streamed completion that ran the server-side tool loop (renamed the local to `resp_metrics`)

---

## [1.5.2] — 2026-06-27

### Added
- **Onboarding tour** — first-run guided tour (`onboarding.service.ts`, `features/onboarding/`) introducing the chat UI to new users
- **Push notifications** — PWA support with `push-notify.service.ts`, web app manifest (`manifest.webmanifest`), service-worker config (`ngsw-config.json`) and app icons

---

## [1.5.0] — 2026-06-27

### Added
- **Authentication & user management** — authentication endpoints and user management (`feat(auth)`)

---

## [1.4.0] — 2026-06-26

### Added — Phase 14: Knowledge & RAG
- **RAG / knowledge base** — upload documents (PDF, TXT, DOCX, Markdown) per profile; text is extracted, chunked (800 chars / 120 overlap), embedded and stored as float32 vectors in SQLite (`kb_documents`, `kb_chunks`)
  - New endpoints: `GET/POST/DELETE /v1/knowledge/documents`, `POST /v1/knowledge/search`
  - New `embedding_service` with a provider fallback chain (`EMBEDDING_CHAIN`, default `ollama:nomic-embed-text,gemini:text-embedding-004,mistral:mistral-embed`)
  - New `rag_service` (extract / chunk / ingest / cosine retrieval in numpy)
  - Chat completions accept `rag`, `rag_top_k`, `profile_id`; retrieved context is folded into the last user message and sources stream back as an SSE `rag_context` frame
  - Web UI: "Knowledge base" sidebar panel (upload/list/delete), RAG ON/OFF toggle, citation chips under grounded replies
- **Telegram reminders** — `/remind <when> <text>` (absolute `HH:MM` or relative `+30m` / `2h` / `1d`), `/reminders`, `/unremind <id>`; persisted in `telegram_reminders` and scheduled on the PTB `JobQueue`, reloaded on restart
- **Telegram multi-language** — `/lang` (inline keyboard or `/lang en|it`); per-chat locale persisted in `telegram_prefs`; strings in `app/telegram/i18n.py` (`it` default, `en`)
- **Diagnostic logging** — RAG retrieval (chunks scanned/matched, top score, dimension-mismatch warnings), context injection, embedding provider used, KB upload/ingest results, and reminder scheduling/delivery

### Changed
- **Keyboard shortcuts** — new conversation shortcut switched to `Alt+N`
- `requirements.txt`: added `numpy`, `python-multipart`, and switched to `python-telegram-bot[job-queue]` (APScheduler) for reminders
- New `TIMEZONE` setting (default `Europe/Rome`) used for reminder parsing/display, independent of the container clock

### Fixed
- Token display conditions now handle `null` values

### Dependencies
- A rebuild of the backend image is required (`docker compose up -d --build backend`) to install the new dependencies

---

## [1.3.1] — 2026-06-26

### Added
- **Tagging & templates** — conversation tagging and prompt template management features

---

## [1.3.0] — 2026-06-24

### Added
- **Nginx reverse proxy** — reverse proxy with TLS support and updated deployment documentation
- **Slash command autocomplete** — autocomplete menu for slash commands in the chat input

---

## [1.2.1] — 2026-06-24

### Added
- **Image generation** — image-to-text and text-to-image generation capabilities
- **User preferences** — user preferences service integrated with the chat page for model and parameter persistence

### Fixed
- Fallback model selection now uses the `_default_model` function

---

## [1.2.0] — 2026-06-24

### Added
- **System prompt** — persistent system instructions in the sidebar, saved to localStorage
- **Model parameters** — temperature (0–2) and max tokens controls in the sidebar

---

## [1.1.2] — 2026-06-16

### Added
- **NVIDIA model discovery** — live model catalog fetch from NVIDIA
- **Ollama model discovery** — live model listing from Ollama `/api/tags` with deduplication against the static YAML catalog

---

## [1.1.1] — 2026-06-14

### Added
- **Multi-MCP orchestrator (agent mode)** — `OrchestratorProvider` routes `agent/*` models to an external OpenAI-compatible sidecar; the sidecar delegates to specialized MCP sub-agents (Proxmox, Synology, Linux SSH, Home Assistant, WatchYourLAN)
- **Telegram `/agent` and `/chat` commands** — toggle between agent mode and normal chat model; remembers the previous model

---

## [1.1.0] — 2026-06-14

### Added
- **Multi-MCP orchestrator support** — new orchestrator provider and configuration options (`ORCHESTRATOR_BASE_URL`, `ORCHESTRATOR_TIMEOUT`)
- **Usage statistics** — `GET /stats` endpoint with global totals, per-profile, per-provider, and per-model breakdowns; Angular `/stats` dashboard with summary cards and expandable tables
- **Conversation search** — SQLite FTS5 virtual table with sync triggers; `GET /conversations/search?q=` endpoint; search bar in sidebar with 300 ms debounce and inline snippet results
- **Tool calling** — server-side execution loop (max 5 iterations); built-in tools (`get_datetime`, `calculator`, `web_search`); `GET /tools` endpoint; SSE `tool_call`/`tool_result` events; toggle in sidebar; tool bubbles in chat
- **Collapsible sidebar sections** — conversations, model, and provider sections can be collapsed
- **Enhanced notifications** — `success` toast type; clickable toasts with navigation callback
- **Chat state management service** — state survives navigation away from the chat page

---

## [1.0.6] — 2026-05-20

### Fixed
- Dockerfile and docker-compose volume paths and health-check endpoint
- Image repository names corrected from `lordraw77` to `lordraw`
- `DOCKER_USER` value fix; added backend/frontend overview documentation
- Frontend build fixes

---

## [1.0.0] — 2026-05-19

### Added
- **Telegram bot** — polling-based bot with per-chat conversation history; streaming replies via progressive message edits; `/start`, `/new`, `/model`, `/models` commands; optional user allowlist via `TELEGRAM_ALLOWED_USERS`
- **Profile system** — named local profiles with no passwords; profile UUID in localStorage; per-profile conversation history; selector modal on first visit; profile switcher in sidebar
- **API key vaulting** — Fernet encryption (AES-128-CBC + HMAC-SHA256); keys stored in SQLite; in-memory cache; vault → env fallback; `PUT`/`DELETE /providers/{id}/key` endpoints
- **Conversation persistence** — SQLite storage via aiosqlite; full message history with telemetry; sidebar conversation list with create/rename/delete
- **LiteLLM provider routing** — Ollama, Groq, Together, Fireworks, HuggingFace support via LiteLLM
- **Provider adapters** — Gemini, Cerebras (with time_info telemetry), Mistral, Cloudflare (emulated streaming), OpenRouter
- **Model discovery endpoints** — Cloudflare, OpenRouter, Gemini, Groq, Cerebras, Mistral
- **Streaming UI via SSE** — token-by-token rendering with cursor animation
- **Provider management page** — list providers, test connectivity, manage API keys
- **Global toast notifications** — `ErrorInterceptor` + `NotificationService` + `ToastContainerComponent`; structured SSE error propagation; HTTP 429 rate-limit mapping
- **Project scaffold** — monorepo (backend + frontend + Docker Compose); FastAPI backend with OpenAI-compatible API; Angular 18 responsive chat shell; Docker Compose development environment
