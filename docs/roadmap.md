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

## Phase 9 — Telegram UX & Voice
- **Inline keyboards for model selection** — `/model` presents tappable buttons instead of requiring the user to type model IDs; callback query handler for selection
- **Voice message support** — receive Telegram voice/audio messages, transcribe via Whisper (Groq / provider), and reply to the transcribed text
- **Quick action buttons** — inline keyboard buttons after each assistant reply: Regenerate, Translate, Summarize, Continue
- **Conversation history in Telegram** — `/history` to list recent conversations; `/search <query>` to full-text search past messages from within Telegram

## Phase 10 — Chat experience
- **Prompt templates library** — saved and reusable system prompts (e.g. "Translate to English", "Code review", "ELI5"); manage via sidebar panel; one-click apply
- **Conversation folders and tags** — organize conversations with color-coded tags or folders; filter sidebar by tag
- **Message bookmarks / pins** — pin important messages inside a conversation; quick-jump to pinned messages
- **Conversation branching / forking** — regenerate keeps both responses as parallel branches; tree navigation to switch between alternatives
- **Drag-and-drop file upload** — drop images (and eventually PDF/text) directly onto the chat area

## Phase 11 — Cross-platform & analytics
- **Telegram ↔ web profile linking** — associate a Telegram user with a web profile so conversations and stats are shared across channels
- **Cost and usage charts** — time-series graphs (daily/weekly) of tokens and costs on the Stats page
- **Inline model comparison** — send the same prompt to 2–3 models in parallel and display responses side by side
- **TTS (text-to-speech)** — play button on assistant messages using Web Speech API
- **Dark / light theme toggle** — switch between dark and light themes, or follow system preference

## Phase 12 — Power user & extensibility
- **Global keyboard shortcuts** — `Ctrl+K` conversation search, `Ctrl+N` new chat, `Ctrl+Shift+S` toggle sidebar
- **Telegram inline query mode** — `@bot query` to get answers directly in any Telegram chat without opening the bot conversation
- **Telegram document upload** — accept PDF, TXT, DOCX files; extract text and send as context to the model
- **Conversation sharing** — generate a read-only shareable link for a conversation; export as image or PDF
- **Custom theme accent color** — let users pick their own accent color in the UI

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
