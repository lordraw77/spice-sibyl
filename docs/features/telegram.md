# Telegram bot

**What it does.** A polling-based bot that exposes the gateway on Telegram: per-chat history, streaming replies with live message editing, model selection, vision, image generation, voice transcription, documents, personal memory, **knowledge base (RAG)**, reminders and web profile linking.

**Configuration.** `TELEGRAM_BOT_TOKEN` in `backend/.env`; optional allowlist with `TELEGRAM_ALLOWED_USERS` (comma-separated user ids). Reminder timezone with `TIMEZONE` (default `Europe/Rome`). Requires the `python-telegram-bot[job-queue]` extra for reminders.

## Commands

| Command | What it does |
|---------|--------------|
| `/start` | welcome message |
| `/new` | new conversation (resets the chat context) |
| `/model` | model selection via a **two-step inline keyboard** (provider → model, with back navigation and ✅ on the current model) |
| `/models` | lists available models |
| `/agent` · `/chat` | switches between agent mode (Multi-MCP orchestrator) and normal chat |
| `/imagine <prompt>` | generates an image (`IMAGE_GENERATION_CHAIN`) and sends it as a photo with a provider/model caption |
| `/history` | last 20 messages of the current session |
| `/search <query>` | full-text search (FTS5) across all saved conversations: titles + snippets |
| `/link` · `/unlink` | generates the code to link/unlink the web profile (see [Authentication and profiles](authentication-and-profiles.md)) |
| `/remind` | reminders: `/remind 15:50 Check backups` or relative `/remind +30m …`, `2h`, `1d` |
| `/reminders` · `/unremind <id>` | lists / cancels pending reminders |
| `/memory on\|off\|list\|del <id>` | personal memory over the linked profile (see [Memory and personalization](memory-and-personalization.md)) |
| `/kb list\|del <id>` | manage the linked profile's knowledge base; to add a document send a file with a **`/kb` caption** (see below) |
| `/rag on\|off` | toggle knowledge-base injection in this chat (per-chat, **OFF by default**) |
| `/lang` · `/lang en\|it` | per-chat bot UI language (inline keyboard or direct); persisted in `telegram_prefs` |

## Media handling

- **Photos** sent to the bot → automatically described by the active model via vision.
- **Voice/audio messages** → transcribed with Groq Whisper (`whisper-large-v3`); the bot shows the transcription, then streams the reply to the transcribed text.
- **Documents** PDF / TXT / DOCX / MD → text is extracted (truncated to 8,000 characters) and used as **one-shot** context for the model, together with any caption. With a `/kb` caption the document is instead **ingested into the knowledge base** (see below).

## Knowledge base (RAG)

Extends the web profile's RAG (see [Knowledge base](knowledge-rag.md)) to the Telegram channel. Requires a **linked web profile** (`/link`): every `/kb`/`/rag` command and `/kb`-captioned upload prompts to link when no profile is connected.

- **Ingestion** — send a **PDF / TXT / DOCX / MD** file with a `/kb` caption: it is added to the linked profile's knowledge base reusing the same pipeline as web uploads (`rag_service.ingest`: extraction → chunking → embedding), with sha256 byte-hash duplicate detection.
- **Management** — `/kb list` shows documents with a status icon (✅ ready · ⏳ pending · ⚠️ error), 🔗 for URL-sourced documents, and chunk count; `/kb del <id>` removes a document by id prefix.
- **Retrieval** — with `/rag on`, each message has `_stream_reply` retrieve the most relevant chunks (`rag_service.retrieve`, hybrid search + optional rerank) and fold them into the last user message; the reply gets a 📚 sources footer (deduplicated filenames). The toggle is **per-chat**, persisted in `telegram_prefs.rag` and reloaded on boot.

## Quick actions

Inline buttons after every reply: **Regenerate** (re-runs the last turn), **Translate** (IT↔EN), **Summarize** (key points), **Continue**.

## Inline mode

`@bot_name question` in any Telegram chat: a direct non-streaming answer (max 300 tokens) as an `InlineQueryResultArticle`, with a 30-second cache.

## Persistent reminders

Reminders are stored in `telegram_reminders` and scheduled on the python-telegram-bot JobQueue: they **survive restarts** (reloaded on boot). Times use `TIMEZONE`, regardless of the container clock.
