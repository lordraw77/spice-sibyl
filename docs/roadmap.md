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

## Phase 5
- **Usage telemetry** ✓ — per-message token counts + latency; provider-reported cost estimates
- **Usage stats / cost tracking dashboard** ✓ — GET /v1/stats (by profile or global); Angular `/stats` page with summary cards + per-provider / per-model tables
- Plugin / tool calling
- Multi-modal support (image input)
- Conversation search / full-text index
