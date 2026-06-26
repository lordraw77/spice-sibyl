# Backend Overview

SpiceSibyl's backend is a **FastAPI async gateway** that exposes an OpenAI-compatible API and routes chat completion requests to any of the supported AI providers with no changes required on the client side.

## Key characteristics

**Provider routing by model prefix** — the correct adapter is selected at request time by inspecting the model ID string (e.g. `groq/…`, `gemini/…`, `cloudflare/…`, `agent/…`). Adding a new provider requires a new adapter class and a one-line entry in the factory; the rest of the stack is unaffected.

**Streaming end-to-end** — all providers expose an async generator. `ChatService` wraps it in a Starlette `EventSourceResponse`, forwarding SSE chunks to the browser as they arrive. Errors that occur after the `200 OK` is sent are emitted as a typed `event: error` SSE frame so the client always receives a structured message.

**Unified telemetry** — every response (streaming and non-streaming) carries a `metrics` block with gateway-measured latency, time-to-first-token, token throughput, and provider-reported cost estimate. Streaming providers emit a final `chat.completion.meta` chunk containing the aggregate telemetry. Non-streaming calls set `first_token_ms` to `null` since the metric is not meaningful in that context.

**Tool calling** — `ChatService.stream()` runs a server-side tool execution loop (max 5 iterations) when tools are present in the request. Each iteration calls `provider.complete()` synchronously, inspects the response for `tool_calls`, executes the matching built-in via `ToolRegistry`, and emits `event: tool_call` and `event: tool_result` SSE frames before sending the final reply. If the loop exhausts all iterations without a final answer it emits an `event: error` SSE frame with a descriptive message. Four built-in tools are available: `get_datetime` (IANA timezone), `calculator` (AST-safe expression eval), `web_search` (DuckDuckGo HTML scraping with instant-answer JSON fallback), and `read_url` (fetches a web page and returns plain-text content up to 4 000 chars).

**Multi-MCP orchestrator (agent mode)** — `OrchestratorProvider` routes any `agent/*` model (e.g. `agent/multi-mcp`) to an external OpenAI-compatible orchestrator sidecar (`ORCHESTRATOR_BASE_URL`). The sidecar delegates to specialized MCP sub-agents (Proxmox, Synology, Linux SSH, Home Assistant, WatchYourLAN) and streams progress frames that map onto the existing `tool_call` / `tool_result` SSE events, so the web UI shows tool bubbles and Telegram shows progressive status edits. `agent/*` models bypass the server-side tool loop — they orchestrate their own sub-agents.

**Conversation search (FTS5)** — a SQLite FTS5 virtual table (`messages_fts`) is kept in sync with the `messages` table via three database triggers (INSERT/DELETE/UPDATE). `GET /conversations/search?q=&profile_id=` runs a prefix-match query and returns `SearchResult[]` with a snippet per hit.

**Conversation export** — `GET /conversations/{id}/export?format=md|json` returns the full conversation as a downloadable Markdown or JSON file. Markdown export includes YAML front-matter (title, model, date) and renders each message under role-based headings.

**Usage stats** — `stats_repository` aggregates message and token counts across the database. `GET /stats` returns global totals plus per-profile, per-provider, and per-model breakdowns, together with in-memory Telegram bot counters.

**API key vault** — provider keys set through the UI are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before being stored in SQLite. The Fernet key is derived from `VAULT_SECRET_KEY`. At startup, all keys are decrypted and cached in memory so every subsequent lookup is O(1) with no database round-trip. If `VAULT_SECRET_KEY` is left at its default placeholder, a `SECURITY` warning is logged at startup to prompt operators to set a strong secret before going to production.

**Real provider connectivity test** — `POST /providers/{id}/test` sends a minimal completion request (`"Reply with the single word: ok"`, max 5 tokens) to cloud providers that have a configured test model, verifying the API key actually works. Ollama is tested via its `/api/tags` endpoint. Providers without a defined test model fall back to key-presence validation.

**Conversation persistence** — after each completed stream the frontend posts the user and assistant messages to `/conversations/{id}/messages`. Messages are stored in SQLite with full telemetry fields and are scoped to a profile UUID, enabling per-user history without authentication overhead. Inserted messages are indexed automatically in `messages_fts` by database trigger.

**Database performance** — indexes on `messages(conversation_id)`, `conversations(profile_id)`, `conversations(updated_at DESC)`, `messages(provider)`, and `messages(role)` ensure fast lookups as the database grows. Migrations are applied idempotently at startup with structured logging for skipped and failed statements.

**Image generation** — `POST /v1/images/generations` generates images from text prompts. The provider chain is configured via `IMAGE_GENERATION_CHAIN` (comma-separated `provider:model` pairs). Each entry is tried in order; unconfigured providers are skipped and failures fall back to the next entry. Supported providers: Gemini (generateContent for Flash Image models, predict for Imagen models), Hugging Face Inference API, Cloudflare Workers AI, Together AI. The service (`image_service.py`) resolves API keys through the same `key_resolver` used by chat providers.

**Vision (image-to-text)** — `ChatMessage.content` accepts the OpenAI multipart format (`[{"type": "text", ...}, {"type": "image_url", ...}]`). Images are base64-encoded by the client and forwarded to vision-capable models through the existing provider pipeline. No backend changes were needed beyond what the schema already supported.

**Telegram bot** — an optional polling-based bot starts alongside the FastAPI server when `TELEGRAM_BOT_TOKEN` is set. It shares the same provider factory and key resolver as the HTTP API, supports per-chat conversation history, streams replies by progressively editing the Telegram message, and maintains in-memory counters (`messages_received`, `messages_sent`, `errors`, `active_chats`) exposed via `GET /stats`. Access can be restricted with `TELEGRAM_ALLOWED_USERS`. The command menu is registered automatically via `set_my_commands` (post-init). Commands: `/start` · `/help` · `/agent` (switch to the `agent/multi-mcp` orchestrator) · `/chat` (switch back to a normal chat model, `/chat <id>` for a specific one) · `/imagine <prompt>` (generate an image) · `/new` · `/model [<id>]` · `/models [<query>]` · `/stats`. `/agent` and `/chat` toggle the active model while remembering the previous chat model. Sending a photo to the bot triggers the vision handler, which downloads the image, base64-encodes it, and sends it to the active model as multipart content.

## Structure

```
app/
├── api/v1/endpoints/   chat · images · knowledge (RAG) · conversations (+ export) · profiles · providers · stats · tools · discovery ×8
├── core/               pydantic-settings (env / .env)
├── db/                 SQLite schema + indexes · conversation / profile / vault / stats / search / kb / telegram_reminder / telegram_prefs repositories
├── dependencies/       provider factory (FastAPI dependency)
├── providers/          BaseProvider · LiteLLM · Gemini · OpenRouter · Cloudflare · Cerebras · Mistral · Orchestrator · Mock
├── schemas/            Pydantic request/response models (chat · conversations · profiles · stats · knowledge)
├── services/           ChatService · ImageService · VaultService · KeyResolver · EmbeddingService · RagService
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
| Telegram | python-telegram-bot v21 | Async-native, integrates cleanly with FastAPI's event loop via manual polling startup |
