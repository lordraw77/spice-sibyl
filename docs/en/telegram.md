# Telegram bot

**What it does.** A polling-based bot that exposes the gateway on Telegram: per-chat history, streaming replies with live message editing, model selection, vision, image generation, voice transcription, documents, personal memory, **knowledge base (RAG)**, reminders and web profile linking.

**Configuration.** `TELEGRAM_BOT_TOKEN` in `backend/.env`; optional allowlist with `TELEGRAM_ALLOWED_USERS` (comma-separated user ids). Reminder timezone with `TIMEZONE` (default `Europe/Rome`). Requires the `python-telegram-bot[job-queue]` extra for reminders.

## Commands

| Command | What it does |
|---------|--------------|
| `/start` | welcome message |
| `/new` | starts a fresh conversation (linked users: a new persisted conversation is created on the next message) |
| `/model` | model selection via a **two-step inline keyboard** (provider → model, with back navigation and ✅ on the current model) |
| `/models` | lists available models |
| `/agent` · `/chat` | switches between agent mode (Multi-MCP orchestrator) and normal chat |
| `/imagine <prompt>` | generates an image (`IMAGE_GENERATION_CHAIN`) and sends it as a photo with a provider/model caption |
| `/history` | **linked users:** recent conversations across both channels, with an inline keyboard to resume any of them; **unlinked:** last 20 messages of the current session |
| `/search <query>` | full-text search (FTS5) across all saved conversations: titles + snippets |
| `/link` · `/unlink` | generates the code to link/unlink the web profile (see [Authentication and profiles](authentication-and-profiles.md)) |
| `/remind` | reminders: `/remind 15:50 Check backups` or relative `/remind +30m …`, `2h`, `1d` |
| `/reminders` · `/unremind <id>` | lists / cancels pending reminders |
| `/memory on\|off\|list\|del <id>` | personal memory over the linked profile (see [Memory and personalization](memory-and-personalization.md)) |
| `/kb list\|del <id>` | manage the linked profile's knowledge base; to add a document send a file with a **`/kb` caption** (see below) |
| `/rag on\|off` | toggle knowledge-base injection in this chat (per-chat, **OFF by default**) |
| `/tool on\|off` | toggle the tool loop for this chat (per-chat, **OFF by default**) |
| `/tools` | list the available tools (grouped) and the current toggle status — view-only, does not change state |
| `/notify on\|off` | mute/unmute Telegram pushes triggered by web events (per-chat, **ON by default**) |
| `/lang` · `/lang en\|it` | per-chat bot UI language (inline keyboard or direct); persisted in `telegram_prefs` |

## Media handling

- **Photos** sent to the bot → automatically described by the active model via vision.
- **Voice/audio messages** → transcribed with Groq Whisper (`whisper-large-v3`); the bot shows the transcription, then streams the reply to the transcribed text.
- **Documents** PDF / TXT / DOCX / MD → text is extracted (truncated to 8,000 characters) and used as **one-shot** context for the model, together with any caption. With a `/kb` caption the document is instead **ingested into the knowledge base** (see below).

## Shared conversation history (Phase 23.a)

For a **linked web profile** (`/link`), Telegram is no longer a separate in-memory chat: every exchange is persisted as a regular profile conversation, so history is shared across both channels.

- **Persistence** — each successful turn (text, voice, photo, document) is stored into the chat's *active conversation*, created lazily on the first message with an auto-generated title. The conversation is tagged `channel='telegram'` and shows up in the **web sidebar with an ✈️ badge**; conversely, conversations you started on the web can be resumed from Telegram.
- **`/history`** — lists the profile's most recent conversations (both channels) as an inline keyboard; tap one to resume it (the active one is marked ✅). The full context is rehydrated so the model continues where you left off — even across a bot restart.
- **`/new`** — detaches the active conversation; the next message starts a fresh one. Switching model (`/model`) or mode (`/agent` / `/chat`) does the same.
- **Unlinked chats** keep the previous in-memory session (last 40 messages), with no cross-channel sync — `/link` to enable it.

Implementation: `telegram_prefs.active_conversation_id` (warm-cached at boot), `conversation_repository.append_messages`, and the new `conversations.channel` column.

## Knowledge base (RAG)

Extends the web profile's RAG (see [Knowledge base](knowledge-rag.md)) to the Telegram channel. Requires a **linked web profile** (`/link`): every `/kb`/`/rag` command and `/kb`-captioned upload prompts to link when no profile is connected.

- **Ingestion** — send a **PDF / TXT / DOCX / MD** file with a `/kb` caption: it is added to the linked profile's knowledge base reusing the same pipeline as web uploads (`rag_service.ingest`: extraction → chunking → embedding), with sha256 byte-hash duplicate detection.
- **Management** — `/kb list` shows documents with a status icon (✅ ready · ⏳ pending · ⚠️ error), 🔗 for URL-sourced documents, and chunk count; `/kb del <id>` removes a document by id prefix.
- **Retrieval** — with `/rag on`, each message has `_stream_reply` retrieve the most relevant chunks (`rag_service.retrieve`, hybrid search + optional rerank) and fold them into the last user message; the reply gets a 📚 sources footer (deduplicated filenames). The toggle is **per-chat**, persisted in `telegram_prefs.rag` and reloaded on boot.

## Tools & MCP (Phase 23.b)

Brings the web chat's **tool loop** to Telegram: with `/tool on`, a completion no longer just streams — the bot merges the built-in tools, the linked profile's **custom tools** and every discovered **MCP tool** (`mcp__<server>__<tool>`, see [MCP](mcp.md)) into the request and runs the shared server-side loop (`ChatService._stream_with_tools`), so behavior is identical across channels.

- **Toggle** — `/tool on|off` flips the tool loop directly. **Per-chat**, **OFF by default**, persisted in `telegram_prefs.tools` and warm-cached at boot (like `/rag`). Profile-aware tools (`kb_search`, `create_reminder`, custom tools) resolve against the linked profile.
- **Listing** — `/tools` lists the available tools grouped by kind (🧩 built-in · 🔌 MCP · 🛠 custom) together with the current toggle status; it is view-only and never changes state (use `/tool` to change it).
- **Progress** — tool calls appear live in the streaming reply (⚙ *tool name* while executing, flipped to ✅ on result).
- **Discovery** — MCP tools are re-probed when you run `/tools` (or when the cache is cold) and cached in `mcp_service`, so ordinary messages don't pay the probe latency.
- **Agent mode** — `agent/*` models orchestrate their own tools; the `/tool` toggle does not apply to them.

## Quick actions

Inline buttons after every reply: **Regenerate** (re-runs the last turn), **Translate** (IT↔EN), **Summarize** (key points), **Continue**.

## Inline mode

`@bot_name question` in any Telegram chat: a direct non-streaming answer (max 300 tokens) as an `InlineQueryResultArticle`, with a 30-second cache.

## Reminders (cross-channel, Phase 23.d)

Reminders are stored in a channel-agnostic `reminders` table and fired by a polling loop in `reminder_service.py` (~20s interval) — they run whether or not the Telegram bot is connected, and **survive restarts**. Times use `TIMEZONE` by default, or a per-reminder timezone override set from the web UI.

- **`/remind <when> <text>`** — accepts everything the old syntax did, plus recurrence and natural-language phrasing:
  - one-shot: `/remind 15:50 Call Mario`, `/remind +30m Check the backups`, `/remind 2h Meeting`, `/remind 2024-06-01 09:00 Trip`
  - natural language (IT/EN): `/remind tomorrow at 9 Dentist`, `/remind domani alle 9 Dentista`, `/remind tra due ore Call back`, `/remind in two hours Call back`, `/remind il 15 alle 14:30 Review`, `/remind dopodomani Follow up`, `/remind stasera Water the plants`, or a bare weekday like `/remind monday Team sync`
  - recurring: `/remind every day 08:00 Take vitamins`, `/remind every monday Weekly meeting`, or a power-user cron with `/remind cron:0,8,*,*,1-5 Weekday alarm` (5 comma-separated fields — `min,hour,dom,mon,dow` — since Telegram splits command args on whitespace)
- **`/remindai <when> <prompt>`** — a **smart reminder**: instead of static text, at fire time it runs the prompt through a small bounded tool loop (max 4 steps, with `fetch_rss` / `get_weather` / `kb_search` / `search_conversations`) and delivers whatever the model produces, e.g. `/remindai every day 08:00 summarize my RSS feeds`.
- **`/reminders`** · **`/unremind <id>`** — unchanged in spirit, now backed by the unified table; `/reminders` shows the recurrence tag (e.g. `[daily]`, `[weekly:mon]`) next to each entry.
- **Snooze / repeat / delete** — a fired reminder on Telegram carries an inline keyboard: 💤 snoozes it by 10 minutes (reschedules `fire_at`, does not affect recurrence), 🔁 re-delivers the same content immediately without touching the schedule, 🗑 deletes the reminder outright (cancels any future recurrence too).
- **Web management** — the Reminders panel on the web UI (`/reminders` route) can create, edit, pause/resume and delete reminders, and set a per-user timezone override, backed by `GET/POST/PATCH/DELETE /v1/reminders`, `POST /v1/reminders/{id}/snooze` and `POST /v1/reminders/{id}/repeat`. A reminder created from the web can target delivery channel `telegram`, `web`, or `both`, with matching snooze/repeat toast actions on `reminderFired` events.

## Cross-channel notifications (Phase 23.c)

For **linked web profiles**, Telegram and the web UI notify each other about relevant events:

- **Web → Telegram** — a workflow run finishing or failing, an image finishing generation, or a long chat reply finishing while the browser tab was hidden trigger a push to the linked chat.
- **Telegram → Web** — a fired reminder or a document ingested via `/kb` appear as a toast/badge in the web sidebar (delivered live over an SSE stream, or picked up on next page load).
- **`/notify on|off`** — mutes/unmutes the Telegram side of the bridge for this chat (**per-chat, ON by default**). The web side has its own per-event-type opt-in matrix in the sidebar's **Notifications** panel (see [Web chat](chat.md#cross-channel-notifications-phase-23c)).

Implementation: `notification_service.py` (`notify_telegram` / `notify_web`), the `notification_events` table, and `telegram_prefs.notify`.
