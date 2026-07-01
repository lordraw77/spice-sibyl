# Backend Overview

SpiceSibyl's backend is a **FastAPI async gateway** that exposes an OpenAI-compatible API and routes chat completion requests to any of the supported AI providers with no changes required on the client side.

## Key characteristics

**Provider routing by model prefix** — the correct adapter is selected at request time by inspecting the model ID string (e.g. `groq/…`, `gemini/…`, `cloudflare/…`, `agent/…`). Adding a new provider requires a new adapter class and a one-line entry in the factory; the rest of the stack is unaffected.

**Streaming end-to-end** — all providers expose an async generator. `ChatService` wraps it in a Starlette `EventSourceResponse`, forwarding SSE chunks to the browser as they arrive. Errors that occur after the `200 OK` is sent are emitted as a typed `event: error` SSE frame so the client always receives a structured message.

**Unified telemetry** — every response (streaming and non-streaming) carries a `metrics` block with gateway-measured latency, time-to-first-token, token throughput, and provider-reported cost estimate. Streaming providers emit a final `chat.completion.meta` chunk containing the aggregate telemetry. Non-streaming calls set `first_token_ms` to `null` since the metric is not meaningful in that context.

**Tool calling** — `ChatService.stream()` runs a server-side tool execution loop (max 5 iterations) when tools are present in the request. Each iteration calls `provider.complete()` synchronously, inspects the response for `tool_calls`, executes the matching tool via `ToolRegistry`, and emits `event: tool_call` and `event: tool_result` SSE frames before sending the final reply. If the loop exhausts all iterations without a final answer it emits an `event: error` SSE frame. Four built-in tools are available: `get_datetime` (IANA timezone), `calculator` (AST-safe expression eval), `web_search` (DuckDuckGo HTML scraping with instant-answer JSON fallback), and `read_url` (fetches a web page and returns plain-text content up to 4 000 chars).

**MCP server management (Phase 18)** — `mcp_service` manages a registry of stdio JSON-RPC MCP servers stored in the `mcp_servers` table. On `refresh()` it spawns each enabled server via `mcp_client` (a minimal stdio JSON-RPC client, no external SDK, Python 3.9-compatible), performs the `initialize` handshake, calls `tools/list`, and builds a routing cache. Discovered tools are namespaced `mcp__<server>__<tool>` and merged into `GET /v1/tools`, so the existing chat tool-loop routes MCP calls transparently. Admin endpoints: `GET/POST /v1/mcp/servers`, `PATCH/DELETE /v1/mcp/servers/{id}` (enable/disable/remove), `POST /v1/mcp/servers/{id}/test`, `POST /v1/mcp/reload`, `GET /v1/mcp/config` (standard `mcpServers` JSON export), `POST /v1/mcp/import` (bulk import from bundle). All mutations are recorded in the audit log.

**Multi-MCP orchestrator (agent mode)** — `OrchestratorProvider` routes any `agent/*` model (e.g. `agent/multi-mcp`) to an external OpenAI-compatible orchestrator sidecar (`ORCHESTRATOR_BASE_URL`). The sidecar delegates to specialized MCP sub-agents and streams progress frames that map onto the existing `tool_call` / `tool_result` SSE events, so the web UI shows tool bubbles and Telegram shows progressive status edits.

**Authentication & access control (Phase 13)** — user accounts with email/password login (bcrypt via `passlib`); role-based permissions (`admin`, `user`, `read-only`); JWT access tokens (30 min, HS256) + rotating refresh tokens (14 d) tracked in `refresh_tokens` for revocation. Auth is **mandatory** on all `/api/v1` routes except the public allowlist (`/auth/*`, `/health`, `GET /shared/{token}`). A bootstrap admin is created on first boot from `ADMIN_EMAIL`/`ADMIN_PASSWORD`. Each user owns N profiles; every profile-scoped endpoint validates ownership via the `resolve_profile` dependency. `AuthService` handles token creation, refresh, revocation, and user management. Audit log (`audit_log` table) records logins, role changes, key updates, deletions, and admin operations with the client IP; viewable by admins via `GET /v1/auth/audit`.

**Rate limiting** — per-user sliding-window limiter (`RATE_LIMIT_DEFAULT`, default `60/minute`) keyed by authenticated user ID; HTTP 429 with `Retry-After` header; implemented in `dependencies/rate_limit.py`.

**RAG / knowledge base (Phase 14)** — `EmbeddingService` extracts text from PDF, TXT, DOCX, Markdown files, chunks it (800 chars / 120 overlap), embeds it via a provider fallback chain (`EMBEDDING_CHAIN`: Ollama `nomic-embed-text` → Gemini → Mistral), and stores float32 BLOB vectors in SQLite (`kb_documents` / `kb_chunks`). `RagService` retrieves top-k chunks by cosine similarity and, when `RAG_HYBRID=true`, fuses results from an FTS5 lexical arm via Reciprocal Rank Fusion; an optional LLM reranker (`RAG_RERANK=llm`, `RAG_RERANK_MODEL`) reorders candidates before context injection. The top-k chunks are folded into the last user message and sources streamed as `event: rag_context`. Endpoints: `GET/POST/DELETE /v1/knowledge/documents`, `POST /v1/knowledge/search`, `POST /v1/knowledge/documents/{id}/reembed`, `GET /v1/knowledge/documents/{id}/chunks`, `GET /v1/knowledge/documents/{id}/source`, `POST /v1/knowledge/urls` (web ingestion).

**Prometheus metrics (Phase 16)** — `GET /api/v1/metrics` (OpenMetrics text format) exposes `sibyl_http_requests_total`, `sibyl_http_request_duration_seconds`, `sibyl_provider_requests_total`, `sibyl_provider_tokens_total{kind}`, `sibyl_provider_latency_seconds`, `sibyl_active_sse_streams`; optional `METRICS_TOKEN` bearer guard. `RequestContextMiddleware` generates a `request_id` (ContextVar) per request, reusing inbound `X-Request-ID` and echoing it on the response for end-to-end tracing. `LOG_FORMAT=json` enables structured JSON logging with the request ID bound.

**Automatic provider fallback for chat completions** — `CHAT_FALLBACK_CHAIN` (same `provider:model` format as image/embedding chains); when a provider errors or times out before emitting any output, the gateway retries the next entry and emits `event: provider_switch` (`{ from, to }`), surfaced as a notice in the web UI. Once tokens have streamed, the error is propagated (no duplicate output).

**DB backup & restore (Phase 16)** — opt-in background task (`BACKUP_ENABLED`, configurable interval + retention) snapshots SQLite via the online backup API to `BACKUP_DIR`. Admin endpoints: `POST /v1/admin/backup`, `GET /v1/admin/backups`, `POST /v1/admin/restore`, plus per-profile `GET /v1/admin/export` / `POST /v1/admin/import` (conversations, messages, KB, templates, tags as a single zip). All recorded in the audit log.

**Health & readiness** — `GET /api/v1/health` (liveness) and `GET /api/v1/ready` (verifies DB + at least one configured provider, returns 503 when degraded); compose defines explicit backend healthchecks and nginx/certbot `depends_on: condition: service_healthy`.

**Conversation search (FTS5)** — a SQLite FTS5 virtual table (`messages_fts`) is kept in sync with the `messages` table via three database triggers (INSERT/DELETE/UPDATE). `GET /conversations/search?q=&profile_id=` runs a prefix-match query and returns `SearchResult[]` with a snippet per hit.

**Conversation sharing** — `POST /conversations/{id}/share` generates a unique token; `GET /shared/{token}` returns a read-only conversation (public, no auth); share button in topbar copies link to clipboard.

**Conversation export** — `GET /conversations/{id}/export?format=md|json` returns the full conversation as a downloadable Markdown or JSON file. Markdown export includes YAML front-matter (title, model, date) and renders each message under role-based headings.

**Prompt templates** — CRUD endpoints (`GET/POST/PATCH/DELETE /v1/templates`) store named system-prompt templates per profile; applied with one click from the sidebar.

**Conversation tags** — color-coded tags on conversations; `GET/POST/PATCH/DELETE /v1/tags` plus `PUT /v1/conversations/{id}/tags` for assignment.

**Message pins** — `POST /v1/conversations/{id}/messages/{msg_id}/pin` toggles pin state; `GET /v1/conversations/{id}/pins` lists pinned messages for the current conversation.

**Conversation branching** — assistant messages carry `parent_id` + `branch_index` + `branch_count`; `GET /v1/conversations/{id}/branches/{parent_id}` returns all siblings for navigation.

**Usage stats** — `stats_repository` aggregates message and token counts. `GET /stats` returns global totals plus per-profile, per-provider, and per-model breakdowns, together with in-memory Telegram bot counters. `GET /v1/stats/daily?range=7d|30d|90d` returns daily time-series for the chart on the Stats page.

**API key vault** — provider keys set through the UI are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before being stored in SQLite. At startup, all keys are decrypted and cached in memory so every subsequent lookup is O(1). If `VAULT_SECRET_KEY` is left at its default placeholder, a `SECURITY` warning is logged.

**Real provider connectivity test** — `POST /providers/{id}/test` sends a minimal completion request to cloud providers, verifying the API key works. Ollama is tested via its `/api/tags` endpoint.

**Conversation persistence** — after each completed stream the frontend posts user and assistant messages to `/conversations/{id}/messages`. Messages are stored in SQLite with full telemetry fields and are scoped to a profile UUID.

**Database performance** — indexes on `messages(conversation_id)`, `conversations(profile_id)`, `conversations(updated_at DESC)`, `messages(provider)`, and `messages(role)` ensure fast lookups. Migrations are applied idempotently at startup.

**Image generation** — `POST /v1/images/generations` generates images from text prompts via `IMAGE_GENERATION_CHAIN`. Supported providers: Gemini, Hugging Face, Cloudflare Workers AI, Together AI.

**Vision (image-to-text)** — `ChatMessage.content` accepts the OpenAI multipart format. Images are base64-encoded by the client and forwarded to vision-capable models through the existing provider pipeline.

**Telegram bot** — polling-based bot that shares the same provider factory and key resolver as the HTTP API. Supports per-chat conversation history, streams replies by progressively editing the Telegram message, inline keyboards for model selection, voice transcription (Groq Whisper), quick action buttons, document upload, inline query mode, scheduled reminders, and multi-language support (it/en). Access can be restricted with `TELEGRAM_ALLOWED_USERS`.

## Structure

```
app/
├── api/v1/endpoints/   chat · images · knowledge (RAG) · conversations (+ export + pins + branches) ·
│                       profiles · providers · stats · tools · discovery ×8 · auth · admin ·
│                       metrics · health · mcp · tags · templates · sharing · telegram_link
├── core/               pydantic-settings (env / .env) · logging_context · metrics
├── db/                 SQLite schema + indexes · conversation / profile / vault / stats / search /
│                       kb / tag / template / share / audit / token / user / mcp /
│                       telegram_link / telegram_prefs / telegram_reminder repositories
├── dependencies/       provider_factory · auth · rate_limit
├── middleware/         request_context (request_id ContextVar, X-Request-ID header)
├── providers/          BaseProvider · LiteLLM · Gemini · OpenRouter · Cloudflare ·
│                       Cerebras · Mistral · NVIDIA · Orchestrator · Mock
├── schemas/            chat · conversations · profiles · stats · knowledge · mcp · auth ·
│                       tags · templates · providers
├── services/           ChatService · ImageService · VaultService · KeyResolver ·
│                       EmbeddingService · RagService · AuthService · BackupService ·
│                       mcp_client · mcp_service · provider_factory
├── tools/              ToolRegistry · built-in tools (get_datetime · calculator · web_search · read_url)
└── telegram/           bot handlers and lifecycle · i18n (it/en)
```

## Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Framework | FastAPI | Native async, automatic OpenAPI docs, dependency injection |
| LLM routing | LiteLLM | Single interface to 100+ providers; Ollama, Groq, Together, etc. |
| Direct HTTP | httpx | Cloudflare, Cerebras and Mistral use non-standard envelopes not supported by LiteLLM |
| Database | SQLite + aiosqlite | Zero-dependency persistence; sufficient for single-instance deployments |
| Full-text search | SQLite FTS5 | Native, zero-dependency full-text indexing with prefix-match and snippets |
| Encryption | `cryptography` (Fernet) | Authenticated encryption; simple key derivation from an arbitrary secret |
| Auth | `passlib` (bcrypt) + `python-jose` (JWT) | Industry-standard hashing and token signing |
| Telegram | python-telegram-bot v21 | Async-native, integrates cleanly with FastAPI's event loop via manual polling startup |
| Metrics | Prometheus OpenMetrics | Standard scraping format; Grafana-compatible dashboard included |
