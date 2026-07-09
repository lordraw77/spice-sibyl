# Changelog

All notable changes to SpiceSibyl are documented in this file. Versions and dates
correspond to the project's git tags.

---

## [Unreleased]

_No unreleased changes. The latest tagged release is [2.1.0]._

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
