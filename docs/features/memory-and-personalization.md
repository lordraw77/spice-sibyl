# Memory & personalization

Phase 19 features: per-profile persistent memory, automatic titles, response cache, reply feedback and the Info page.

## Per-profile persistent memory

**What it does.** SpiceSibyl remembers facts about you across conversations (preferences, personal facts, ongoing projects, standing instructions). After each persisted exchange, an async low-cost LLM call (`MEMORY_EXTRACTION_MODEL`, default = `DEFAULT_MODEL`) extracts noteworthy information and consolidates it into the `profile_memories` table (automatic dedup, capped at `MEMORY_MAX_ITEMS` memories). When memory is on, enabled memories are compacted into a `<user_memory>` block appended to the system prompt (`MEMORY_MAX_CHARS` character budget, most recent first).

**How to use it.**
- Dedicated **Memoria 🧠** page (`/memory`, **Risorse → Memoria** in the navbar, or the *Gestisci →* link next to the Memory switch in the sidebar): memory list with category (⭐ preference, 💡 fact, 📁 project, 📌 instruction), manual add with category choice, per-memory enable/disable or delete, **Forget all**. The **automatic memory extraction (profile)** checkbox — the *profile-level* switch (when OFF there is no extraction and no injection for the whole profile) — also lives here.
- The **Memoria ON/OFF** toggle in the sidebar **Funzioni** section is the *per-chat* (incognito) switch: when OFF, new requests neither use nor feed memory.
- Replies personalized with memory show the **🧠 memoria** chip under the message.

**From Telegram.** `/memory on|off` toggles memory in the current chat (persisted in `telegram_prefs`); `/memory list` shows the memories of the web profile linked via `/link`; `/memory del <id>` forgets one. Injection and extraction only work for linked users.

**Configuration.**

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORY_ENABLED` | `true` | Global feature switch |
| `MEMORY_EXTRACTION_MODEL` | *(empty = `DEFAULT_MODEL`)* | Model for the async extraction call |
| `MEMORY_MAX_CHARS` | `2000` | Character budget of the injected block |
| `MEMORY_MAX_ITEMS` | `100` | Max memories per profile |

API: `GET/POST /v1/memories`, `PATCH/DELETE /v1/memories/{id}`, `DELETE /v1/memories` (forget all), `GET/PUT /v1/memories/settings`.

## Automatic titles (LLM auto-titling)

**What it does.** After a conversation's first persisted exchange, a background task generates a concise title (max 6 words, in the conversation's language) replacing the old "first 60 chars of the first message" heuristic. The conversation list (Conversations panel) refreshes on its own a few seconds later.

**Configuration.** `AUTO_TITLE_ENABLED` (default `true`), `TITLE_MODEL` (empty = `MEMORY_EXTRACTION_MODEL`, then `DEFAULT_MODEL`).

## Response cache

**What it does.** Completed replies go into an in-memory LRU cache keyed exactly on model + messages + temperature + max tokens. An identical request within the TTL skips the provider entirely: the reply is replayed in one shot with the **⚡ cache** chip and zero latency. Requests with tools, `agent/*` models and multimodal content (images) are never cached.

**Configuration.** `RESPONSE_CACHE_ENABLED` (default `true`), `RESPONSE_CACHE_TTL_SECONDS` (default `600`), `RESPONSE_CACHE_MAX_ENTRIES` (default `256`). Hit/miss stats are visible on the **Info** page.

## Reply feedback (👍/👎)

**What it does.** Every persisted assistant reply can be rated thumbs up/down (optional note on 👎). Ratings feed an exportable dataset for offline model evaluation.

**How to use it.**
- Hover over a reply: 👍 and 👎 appear among the actions. Clicking the active icon again clears the rating.
- Export the dataset from `GET /v1/feedback/export`: every rated reply is paired with the prompt that generated it (message id, model, provider, rating, note).
- Regression harness: `backend/scripts/eval_regression.py` re-runs 👍-rated prompts against the gateway and flags replies that drift too far from the approved ones.

```bash
python backend/scripts/eval_regression.py dataset.json \
  --base-url http://localhost:8800/api/v1 \
  --email admin@example.com --password ... [--model groq/llama-3.1-8b-instant]
```

## Info page

**What it does.** The **Info** navbar entry opens a page with: web UI version (from the build-time `package.json`), backend version/environment/uptime (`GET /v1/info`), default model, database (path and size), API endpoints in use (base URL, health, readiness, metrics, OpenAPI docs link), live READY/DEGRADED status and the list of enabled features with cache statistics.

**Configuration.** The backend version comes from `APP_VERSION` (default aligned with the release); Docker builds stamp it automatically from the release tag (`make release VERSION=v1.9.0`).
