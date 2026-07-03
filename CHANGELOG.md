# Changelog

All notable changes to SpiceSibyl are documented in this file. Versions and dates
correspond to the project's git tags.

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
- **Feature documentation** — new "Memoria e personalizzazione" / "Memory & personalization" pages in `docs/funzionalita/` and `docs/features/` (memory, auto-titling, cache, feedback, Info page) and the built-in tools tables updated with the 8 new Phase 19 tools

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
