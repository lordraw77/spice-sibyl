# Backend Overview

SpiceSibyl's backend is a **FastAPI async gateway** that exposes an OpenAI-compatible API and routes chat completion requests to any of the supported AI providers with no changes required on the client side.

## Key characteristics

**Provider routing by model prefix** — the correct adapter is selected at request time by inspecting the model ID string (e.g. `groq/…`, `gemini/…`, `cloudflare/…`). Adding a new provider requires a new adapter class and a one-line entry in the factory; the rest of the stack is unaffected.

**Streaming end-to-end** — all providers expose an async generator. `ChatService` wraps it in a Starlette `EventSourceResponse`, forwarding SSE chunks to the browser as they arrive. Errors that occur after the `200 OK` is sent are emitted as a typed `event: error` SSE frame so the client always receives a structured message.

**Unified telemetry** — every response (streaming and non-streaming) carries a `metrics` block with gateway-measured latency, time-to-first-token, token throughput, and provider-reported cost estimate. Streaming providers emit a final `chat.completion.meta` chunk containing the aggregate telemetry.

**Tool calling** — `ChatService.stream()` runs a server-side tool execution loop (max 5 iterations) when tools are present in the request. Each iteration calls `provider.complete()` synchronously, inspects the response for `tool_calls`, executes the matching built-in via `ToolRegistry`, and emits `event: tool_call` and `event: tool_result` SSE frames before sending the final reply. Three built-in tools are available: `get_datetime` (IANA timezone), `calculator` (AST-safe expression eval), and `web_search` (DuckDuckGo JSON API).

**Conversation search (FTS5)** — a SQLite FTS5 virtual table (`messages_fts`) is kept in sync with the `messages` table via three database triggers (INSERT/DELETE/UPDATE). `GET /conversations/search?q=&profile_id=` runs a prefix-match query and returns `SearchResult[]` with a snippet per hit.

**Usage stats** — `stats_repository` aggregates message and token counts across the database. `GET /stats` returns global totals plus per-profile, per-provider, and per-model breakdowns, together with in-memory Telegram bot counters.

**API key vault** — provider keys set through the UI are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before being stored in SQLite. The Fernet key is derived from `VAULT_SECRET_KEY`. At startup, all keys are decrypted and cached in memory so every subsequent lookup is O(1) with no database round-trip.

**Conversation persistence** — after each completed stream the frontend posts the user and assistant messages to `/conversations/{id}/messages`. Messages are stored in SQLite with full telemetry fields and are scoped to a profile UUID, enabling per-user history without authentication overhead. Inserted messages are indexed automatically in `messages_fts` by database trigger.

**Telegram bot** — an optional polling-based bot starts alongside the FastAPI server when `TELEGRAM_BOT_TOKEN` is set. It shares the same provider factory and key resolver as the HTTP API, supports per-chat conversation history, streams replies by progressively editing the Telegram message, and maintains in-memory counters (`messages_received`, `messages_sent`, `errors`, `active_chats`) exposed via `GET /stats`. The `/stats` and `/models` commands are supported; `/models` accepts an optional filter (e.g. `/models groq`, `/models vision`).

## Structure

```
app/
├── api/v1/endpoints/   chat · conversations · profiles · providers · stats · tools · discovery ×6
├── core/               pydantic-settings (env / .env)
├── db/                 SQLite schema · conversation / profile / vault / stats / search repositories
├── dependencies/       provider factory (FastAPI dependency)
├── providers/          BaseProvider · LiteLLM · Gemini · OpenRouter · Cloudflare · Cerebras · Mistral · Mock
├── schemas/            Pydantic request/response models (chat · conversations · profiles · stats)
├── services/           ChatService · VaultService · KeyResolver
├── tools/              ToolRegistry · built-in tools (get_datetime · calculator · web_search)
└── telegram/           bot handlers and lifecycle
```

## New files added in Phase 5

| File | Purpose |
|---|---|
| `app/schemas/stats.py` | Pydantic models for the stats response |
| `app/db/stats_repository.py` | SQL aggregation queries for usage stats |
| `app/db/search_repository.py` | FTS5 search queries |
| `app/tools/__init__.py` | Package init |
| `app/tools/builtin.py` | `get_datetime`, `calculator`, `web_search` implementations |
| `app/tools/registry.py` | `ToolRegistry` — lookup and dispatch by tool name |
| `app/api/v1/endpoints/stats.py` | `GET /stats` endpoint |
| `app/api/v1/endpoints/tools.py` | `GET /tools` endpoint |

## Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Framework | FastAPI | Native async, automatic OpenAPI docs, dependency injection |
| LLM routing | LiteLLM | Single interface to 100+ providers; Ollama, Groq, Together, etc. |
| Direct HTTP | httpx | Cloudflare, Cerebras and Mistral use non-standard envelopes not supported by LiteLLM |
| Database | SQLite + aiosqlite | Zero-dependency persistence; sufficient for single-instance deployments |
| Full-text search | SQLite FTS5 | Native, zero-dependency full-text indexing with prefix-match and snippets |
| Encryption | `cryptography` (Fernet) | Authenticated encryption; simple key derivation from an arbitrary secret |
| Telegram | python-telegram-bot v21 | Async-native, integrates cleanly with FastAPI's event loop via manual polling startup |
