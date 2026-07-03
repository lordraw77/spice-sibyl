# Telegram bot

**What it does.** A polling-based bot that exposes the gateway on Telegram: per-chat history, streaming replies with live message editing, model selection, vision, image generation, voice transcription, documents, reminders and web profile linking.

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
| `/lang` · `/lang en\|it` | per-chat bot UI language (inline keyboard or direct); persisted in `telegram_prefs` |

## Media handling

- **Photos** sent to the bot → automatically described by the active model via vision.
- **Voice/audio messages** → transcribed with Groq Whisper (`whisper-large-v3`); the bot shows the transcription, then streams the reply to the transcribed text.
- **Documents** PDF / TXT / DOCX → text is extracted (truncated to 8,000 characters) and used as context for the model, together with any caption.

## Quick actions

Inline buttons after every reply: **Regenerate** (re-runs the last turn), **Translate** (IT↔EN), **Summarize** (key points), **Continue**.

## Inline mode

`@bot_name question` in any Telegram chat: a direct non-streaming answer (max 300 tokens) as an `InlineQueryResultArticle`, with a 30-second cache.

## Persistent reminders

Reminders are stored in `telegram_reminders` and scheduled on the python-telegram-bot JobQueue: they **survive restarts** (reloaded on boot). Times use `TIMEZONE`, regardless of the container clock.
