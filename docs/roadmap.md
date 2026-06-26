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

## Phase 7 ✓
- **Image-to-text (vision)** — upload images in the web chat (click, drag, paste) or send photos to the Telegram bot; images are base64-encoded and forwarded as OpenAI-compatible multipart content to any vision-capable model (Gemini, Groq/Llama-4-Scout, etc.)
- **Text-to-image generation** — `/imagine <prompt>` command in both web UI and Telegram; configurable provider fallback chain via `IMAGE_GENERATION_CHAIN` env var (format: `provider:model,...`); supported providers: Gemini (Imagen / Flash Image), Hugging Face (FLUX.1-schnell), Cloudflare Workers AI (SDXL), Together AI (FLUX.1-schnell-Free)
- **Image generation endpoint** — `POST /v1/images/generations` with automatic provider fallback; each entry in the chain is tried in order, skipping unconfigured providers and falling back on errors
- **Telegram `/imagine`** — generates an image and sends it as a Telegram photo with provider/model caption
- **Telegram photo handler** — photos sent to the bot are automatically described by the active model via vision

## Phase 8 ✓
- **Nginx reverse proxy** — unified `nginx` service in `docker-compose.prod.yml` serves the Angular static build on `/` and proxies `/api` to the backend; frontend and backend no longer exposed on separate ports
- **Relative API URL** — default `apiUrl` changed to `/api/v1` so the browser always talks to the same host it loaded the page from; no more hardcoded IPs in `app-config.json`
- **Dynamic CORS** — `PUBLIC_URL` env var (e.g. `https://sibyl.example.com`) automatically added to `cors_origins` alongside the default `localhost` entries; both local dev and DDNS access work without duplicating config
- **HTTPS / TLS termination** — entrypoint auto-detects TLS certs in `nginx/ssl/`; optional Certbot sidecar for Let's Encrypt; graceful fallback to HTTP-only when no certs are mounted
- **Production Dockerfile** — multi-stage build (`node:20-alpine` → `ng build --configuration production` → `nginx:1.27-alpine`); eliminates the dev-server in production
- **Environment documentation** — deployment guide (`docs/deploy.md`) rewritten with `PUBLIC_URL`, `VAULT_SECRET_KEY`, DDNS setup, TLS options, and architecture diagram

## Phase 9 ✓
- **Inline keyboards for model selection** — `/model` presents a two-step inline keyboard (provider → model) with tappable buttons; callback query handlers for provider selection, model selection, and back-navigation; current model highlighted with ✅
- **Voice message support** — receive Telegram voice/audio messages, transcribe via Groq Whisper (`whisper-large-v3`), show transcription, then stream the LLM reply to the transcribed text
- **Quick action buttons** — inline keyboard buttons after each assistant reply: Regenerate (re-runs last turn), Translate (IT↔EN), Summarize (key points), Continue; shared streaming helper (`_stream_reply`) refactored from the message handler
- **Conversation history in Telegram** — `/history` lists the last 20 messages in the current in-memory session; `/search <query>` performs full-text search (FTS5) across all saved conversations and returns titles + snippets

## Phase 10 ✓
- **Prompt templates library** — saved and reusable system prompts (e.g. "Translate to English", "Code review", "ELI5"); manage via sidebar panel; one-click apply; save current system prompt as template
- **Conversation folders and tags** — color-coded tags on conversations; tag filter bar in sidebar; tag manager section to create/edit/delete tags; assign tags to conversations via popover
- **Message bookmarks / pins** — pin important messages inside a conversation; pinned messages bar above chat for quick-jump; toggle pin via hover action button
- **Conversation branching / forking** — regenerate keeps both responses as parallel branches; `< 1/3 >` branch navigation arrows on assistant messages; switch between alternatives; branches persisted with parent_id + branch_index in SQLite
- **Drag-and-drop file upload** — drop images directly onto the chat area with visual overlay; validates type (image/*) and size (20 MB max)

## Phase 11 ✓
- **Telegram ↔ web profile linking** — `/link` command generates a 6-char code; paste in web sidebar to associate Telegram user with web profile; `/unlink` to disconnect; linked users share conversations and stats across channels
- **Cost and usage charts** — daily time-series charts on the Stats page (tokens area chart + cost bar chart); switchable 7d/30d/90d range; `GET /v1/stats/daily` endpoint with SQLite date aggregation
- **Inline model comparison** — new `/compare` page; select 2–4 models, send the same prompt, stream responses in parallel side-by-side columns with per-model telemetry (latency, tokens, cost)
- **TTS (text-to-speech)** — play/stop button on assistant messages using Web Speech API (`SpeechSynthesisUtterance`); strips markdown before speaking; Italian language default
- **Dark / light theme toggle** — CSS custom properties system (`--bg-primary`, `--text-primary`, `--accent`, etc.); `ThemeService` with dark/light/system modes; toggle button in navbar (sun/moon icon); preference stored in localStorage; `[data-theme]` attribute on `<html>`

## Phase 12 ✓
- **Global keyboard shortcuts** — `Ctrl+K` conversation search (opens sidebar + focuses search input), `Ctrl+N` new chat, `Ctrl+Shift+S` toggle sidebar; guards against firing in input fields (except Ctrl+K)
- **Telegram inline query mode** — `@bot query` to get answers directly in any Telegram chat; non-streaming LLM call with 300 max tokens; `InlineQueryResultArticle` with 30s cache
- **Telegram document upload** — accept PDF (`PyPDF2`), TXT, DOCX (`python-docx`) files; extract text (truncated to 8000 chars); send as context to the active model with caption; streamed response via `_stream_reply`
- **Conversation sharing** — `POST /v1/conversations/{id}/share` generates a unique token; `GET /v1/shared/{token}` returns read-only conversation (public, no auth); share button in topbar copies link to clipboard; `/shared/:token` route with `SharedViewComponent` (minimal read-only layout with markdown rendering + syntax highlighting)
- **Custom theme accent color** — `ThemeService` extended with `setAccent()`/`resetAccent()`; dynamically updates all `--accent-*` CSS custom properties; navbar accent picker with 8 preset color swatches + `<input type="color">`; persisted in localStorage; works across dark and light themes

## Phase 13 — Security & access
- **Authentication and access control** — user accounts with login (email/password or OAuth); role-based permissions (admin, user, read-only); JWT session tokens
- **Rate limiting** — per-user and per-provider request rate limits; HTTP 429 with `Retry-After` header; rate-limit visibility in both web UI and Telegram
- **Audit log** — record who did what and when (model changes, key updates, conversation deletions); viewable by admins

## Phase 14 — Knowledge & RAG
- **RAG / document ingestion** — upload documents (PDF, TXT, DOCX, Markdown) to a knowledge base; chunk, embed, and store in a vector index; retrieve relevant context at query time and inject into the prompt
- **Telegram scheduled reminders** — `/remind 18:00 Check backups` schedules a message; bot sends it at the specified time with optional LLM-generated context
- **Telegram multi-language support** — `/lang en|it|...` switches the bot's UI language per chat; all bot messages and command descriptions adapt to the selected locale

## Phase 15 — Mobile & polish
- **Mobile-optimized layout** — responsive redesign of sidebar, chat area, and composer for small screens; swipe gestures for sidebar toggle; touch-friendly action buttons
- **PWA support** — installable progressive web app with offline shell, push notifications for long-running generations, and home-screen icon
- **Onboarding flow** — first-time guided tour highlighting key features (model selection, tools, system prompt, slash commands)
