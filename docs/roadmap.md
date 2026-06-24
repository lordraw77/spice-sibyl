# SpiceSibyl Roadmap

## Phase 1 ✓
- Monorepo scaffold
- FastAPI OpenAI-compatible mock API
- Angular chat shell
- Docker Compose

## Phase 2 ✓
- LiteLLM real provider routing (Ollama, Groq, Mistral, Together, Fireworks, HuggingFace)
- Streaming UI via SSE
- Provider management page (GET /providers, POST /providers/{id}/test)
- GeminiProvider — dedicated adapter for Google Generative AI
- Cerebras + Mistral providers (direct HTTP, no LiteLLM)
- Model discovery endpoints × 6 (Cloudflare, OpenRouter, Gemini, Groq, Cerebras, Mistral)
- Global toast notification system (ErrorInterceptor + NotificationService + ToastContainerComponent)
- Structured SSE error propagation (event: error frame from backend → toast + bubble message)
- HTTP 429 mapping for rate-limit errors

## Phase 3 ✓
- **Conversation persistence** — SQLite via aiosqlite; full message history with telemetry saved after each exchange; sidebar conversation list with create/delete
- **API key vaulting** — Fernet (AES-128-CBC + HMAC-SHA256) encryption; keys stored in SQLite; in-memory cache; vault→env fallback in all providers; PUT + DELETE /providers/{id}/key
- **Profile system** — named local profiles (no passwords); profile UUID stored in localStorage; per-profile conversation history; profile selector modal on first visit; profile switcher in sidebar

## Phase 4 ✓
- **Telegram bot** — polling-based; per-chat conversation history; streaming replies with live edit; `/start`, `/new`, `/model`, `/models`; optional user allowlist (`TELEGRAM_ALLOWED_USERS`)

## Phase 5 ✓
- **Usage telemetry** — per-message token counts + latency; provider-reported cost estimates
- **Usage stats / cost tracking dashboard** — GET /v1/stats (by profile or global); Angular `/stats` page with summary cards + per-provider / per-model tables
- **Tool calling** — server-side tool execution loop (max 5 iterations); 3 built-in tools (get_datetime, calculator, web_search via DuckDuckGo); GET /v1/tools; SSE tool_call/tool_result events; toggle ON/OFF in sidebar; tool bubbles in chat
- **Conversation search / full-text index** — SQLite FTS5 with sync triggers; GET /v1/conversations/search?q=; search bar in sidebar with 300 ms debounce, inline results with snippets, Escape to close
- **Multi-MCP orchestrator (agent mode)** — OrchestratorProvider routes `agent/*` models to an external sidecar; Telegram `/agent` and `/chat` commands to toggle modes
- **NVIDIA and Ollama model discovery** — live model catalog fetch for NVIDIA and Ollama providers

## Phase 6 ✓
- **System prompt** — persistent system instructions stored in localStorage; collapsible sidebar section; save/clear actions
- **Model parameters** — temperature slider (0–2) and max tokens input exposed in the sidebar; sent with every completion request
- **Conversation export** — GET /conversations/{id}/export?format=md|json; download buttons in the topbar (Markdown and JSON)
- **Message actions** — copy message to clipboard, regenerate last assistant response, edit last user message; hover-to-reveal action buttons on every message
- **Voice input** — Web Speech API integration; microphone button in the composer with visual pulse animation while listening
- **Stream cancellation** — stop button replaces the send button during streaming; aborts the in-flight request and resets the UI
- **Syntax highlighting** — highlight.js integration for code blocks in assistant responses; language-aware rendering via custom marked renderer
- **`read_url` tool** — new built-in tool that fetches a web page and returns plain-text content (HTML stripped); up to 4 000 chars; registered in the tool registry
- **Improved `web_search`** — primary strategy switched to DuckDuckGo HTML scraping for richer snippets; falls back to the instant-answer JSON API
- **Real provider connectivity test** — POST /providers/{id}/test now sends a minimal completion request to cloud providers instead of only checking key presence
- **Database performance indexes** — added indexes on messages(conversation_id), conversations(profile_id), conversations(updated_at), messages(provider), messages(role)
- **Hardened error handling** — bare `except Exception` replaced with specific exception types across chat service, tools, and providers; improved logging levels (debug for normal ops, warning for failures)
- **Vault security warning** — startup logs a SECURITY warning when VAULT_SECRET_KEY is still set to the default placeholder

## Backlog
- Multi-modal support (image input)
- RAG / document ingestion
- Prompt templates library
- Conversation branching / forking
- Mobile-optimized layout
- Authentication and access control
