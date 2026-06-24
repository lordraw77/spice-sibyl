# Changelog

All notable changes to SpiceSibyl are documented in this file.

---

## [0.6.0] — 2026-06-24

### Added
- **System prompt** — persistent system instructions in the sidebar, saved to localStorage
- **Model parameters** — temperature (0–2) and max tokens controls in the sidebar
- **Conversation export** — `GET /conversations/{id}/export?format=md|json` endpoint; download buttons in the topbar
- **Message actions** — copy to clipboard, regenerate last response, edit last user message (hover-to-reveal buttons)
- **Voice input** — Web Speech API integration with microphone button and pulse animation
- **Stream cancellation** — stop button to abort in-flight streaming requests
- **Syntax highlighting** — highlight.js integration for code blocks in assistant responses
- **`read_url` tool** — new built-in tool that fetches a web page and returns plain-text content (max 4 000 chars)
- **Real provider connectivity test** — `POST /providers/{id}/test` sends a minimal completion to verify API keys work
- **Database indexes** — added indexes on `messages(conversation_id)`, `conversations(profile_id)`, `conversations(updated_at)`, `messages(provider)`, `messages(role)`
- **Vault security warning** — startup logs a SECURITY warning when `VAULT_SECRET_KEY` is the default placeholder

### Changed
- **`web_search` tool** — primary strategy switched to DuckDuckGo HTML scraping for richer snippets; instant-answer JSON API is now the fallback
- **Error handling** — bare `except Exception` replaced with specific exception types across chat service, tools, and providers
- **Logging** — normal operations downgraded from `warning` to `debug`; structured logging for migration skips
- **Non-streaming telemetry** — `first_token_ms` set to `null` for non-streaming completions (not meaningful)
- **Rename optimization** — `PATCH /conversations/{id}` uses a lightweight existence check instead of loading all messages

### Fixed
- Streaming response body null check in frontend `ChatService`
- Missing newline at end of `litellm_provider.py`

---

## [0.5.0] — 2026-06-16

### Added
- **NVIDIA model discovery** — live model catalog fetch from NVIDIA
- **Ollama model discovery** — live model listing from Ollama `/api/tags` with deduplication against the static YAML catalog

---

## [0.4.1] — 2026-06-14

### Added
- **Multi-MCP orchestrator (agent mode)** — `OrchestratorProvider` routes `agent/*` models to an external OpenAI-compatible sidecar; the sidecar delegates to specialized MCP sub-agents (Proxmox, Synology, Linux SSH, Home Assistant, WatchYourLAN)
- **Telegram `/agent` and `/chat` commands** — toggle between agent mode and normal chat model; remembers the previous model
- **`ORCHESTRATOR_BASE_URL` and `ORCHESTRATOR_TIMEOUT` configuration** — connect the gateway to the orchestrator sidecar

---

## [0.4.0] — 2026-05-20

### Added
- **Usage statistics** — `GET /stats` endpoint with global totals, per-profile, per-provider, and per-model breakdowns; Angular `/stats` dashboard with summary cards and expandable tables
- **Conversation search** — SQLite FTS5 virtual table with sync triggers; `GET /conversations/search?q=` endpoint; search bar in sidebar with 300 ms debounce and inline snippet results
- **Tool calling** — server-side execution loop (max 5 iterations); 3 built-in tools (`get_datetime`, `calculator`, `web_search`); `GET /tools` endpoint; SSE `tool_call`/`tool_result` events; toggle in sidebar; tool bubbles in chat
- **Collapsible sidebar sections** — conversations, model, and provider sections can be collapsed
- **Enhanced notifications** — `success` toast type; clickable toasts with navigation callback
- **Chat state management service** — state survives navigation away from the chat page

---

## [0.3.0] — 2026-05-19

### Added
- **Telegram bot** — polling-based bot with per-chat conversation history; streaming replies via progressive message edits; `/start`, `/new`, `/model`, `/models` commands; optional user allowlist via `TELEGRAM_ALLOWED_USERS`
- **Profile system** — named local profiles with no passwords; profile UUID in localStorage; per-profile conversation history; selector modal on first visit; profile switcher in sidebar
- **API key vaulting** — Fernet encryption (AES-128-CBC + HMAC-SHA256); keys stored in SQLite; in-memory cache; vault → env fallback; `PUT`/`DELETE /providers/{id}/key` endpoints
- **Conversation persistence** — SQLite storage via aiosqlite; full message history with telemetry; sidebar conversation list with create/rename/delete

---

## [0.2.0] — 2026-05-19

### Added
- **LiteLLM provider routing** — Ollama, Groq, Together, Fireworks, HuggingFace support via LiteLLM
- **GeminiProvider** — dedicated adapter for Google Generative AI
- **CerebrasProvider** — direct HTTP adapter with time_info telemetry
- **MistralProvider** — direct HTTP adapter
- **CloudflareProvider** — direct HTTP adapter with emulated streaming
- **OpenRouterProvider** — LiteLLM-based adapter
- **Model discovery endpoints** — Cloudflare, OpenRouter, Gemini, Groq, Cerebras, Mistral (6 providers)
- **Streaming UI via SSE** — token-by-token rendering with cursor animation
- **Provider management page** — list providers, test connectivity, manage API keys
- **Global toast notifications** — `ErrorInterceptor` + `NotificationService` + `ToastContainerComponent`
- **Structured SSE error propagation** — `event: error` frame from backend rendered as toast + chat bubble
- **HTTP 429 rate-limit mapping**

---

## [0.1.0] — 2026-05-18

### Added
- Monorepo scaffold (backend + frontend + Docker Compose)
- FastAPI backend with OpenAI-compatible mock API
- Angular 18 chat shell with responsive layout
- Docker Compose development environment
- Cloudflare and OpenRouter discovery endpoints
