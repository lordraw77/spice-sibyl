# Phase 14 — Knowledge & RAG — Implementation Plan

> Status: planned · Target: Phase 14 of `docs/roadmap.md`
> Decision: **RAG over the existing SQLite DB**, embeddings via already-integrated providers
> (Ollama `nomic-embed-text` default, Gemini / Mistral fallback), cosine similarity in Python.

Phase 14 has three independent deliverables:

1. **RAG / document ingestion** — knowledge base with embed + retrieve, injected at query time.
2. **Telegram scheduled reminders** — `/remind 18:00 <text>`.
3. **Telegram multi-language support** — `/lang en|it|...` per-chat UI locale.

Each can ship as its own PR. RAG is the bulk of the work.

---

## 1. RAG / document ingestion

### 1.1 Data model (SQLite — `backend/app/db/database.py`)

Add to `_SCHEMA` (idempotent `CREATE TABLE IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS kb_documents (
    id          TEXT    PRIMARY KEY,
    profile_id  TEXT    NOT NULL DEFAULT 'default',
    filename    TEXT    NOT NULL,
    mime        TEXT,
    size_bytes  INTEGER,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'pending', -- pending|ready|error
    error       TEXT,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id           TEXT    PRIMARY KEY,
    document_id  TEXT    NOT NULL,
    profile_id   TEXT    NOT NULL DEFAULT 'default',
    chunk_index  INTEGER NOT NULL,
    content      TEXT    NOT NULL,
    embedding    BLOB,                  -- float32 vector, numpy.tobytes()
    embed_model  TEXT,
    created_at   INTEGER NOT NULL,
    FOREIGN KEY (document_id) REFERENCES kb_documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_kb_chunks_profile ON kb_chunks(profile_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_document ON kb_chunks(document_id);
```

Vectors stored as `float32` BLOB via `numpy.ndarray.tobytes()` / `np.frombuffer`.
No migration entry needed (new tables), but keep the pattern consistent.

### 1.2 New dependency

- `numpy` → add to `backend/requirements.txt` (cosine similarity, vector packing).
  Text extraction reuses existing `PyPDF2` + `python-docx` (already present for the
  Telegram document handler); Markdown/TXT are read as plain text.

### 1.3 Embedding service — `backend/app/services/embedding_service.py` (new)

A small provider-agnostic embedder mirroring the image-generation fallback-chain style
(`settings.image_generation_chain`):

- New setting `embedding_chain` in `core/config.py`, default:
  `"ollama:nomic-embed-text,gemini:text-embedding-004,mistral:mistral-embed"`.
- `async def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]`
  returns vectors + the model id that produced them; tries each chain entry in order,
  skipping unconfigured providers (reuse `key_resolver` for keys), falling back on error.
- Ollama via `POST {ollama_api_base}/api/embeddings`; Gemini/Mistral via their REST
  embed endpoints with `httpx` (same style as existing direct providers).
- Batches chunk lists; logs at debug/warning consistent with the rest of the codebase.

### 1.4 Ingestion + retrieval — `backend/app/services/rag_service.py` (new)

- `extract_text(filename, raw: bytes) -> str` — dispatch by extension:
  `.pdf` (PyPDF2), `.docx` (python-docx), `.md`/`.txt`/`.markdown` (decode utf-8).
- `chunk_text(text, size=800, overlap=120) -> list[str]` — word/char window with overlap.
- `async def ingest(db, profile_id, filename, raw)` —
  insert `kb_documents` row (`pending`) → extract → chunk → `embed_texts` →
  bulk-insert `kb_chunks` → update doc `chunk_count` + `status=ready`
  (set `status=error` + message on failure).
- `async def retrieve(db, profile_id, query, top_k=4, min_score=0.2) -> list[Chunk]` —
  embed the query, load this profile's chunk vectors, cosine top-k in numpy.
  (Profile-scoped scan is fine for the expected corpus size; sqlite-vec is the future
  upgrade path if collections grow — noted, not built now.)

### 1.5 Repository — `backend/app/db/kb_repository.py` (new)

Follows `template_repository.py` conventions (plain async functions taking `db`):
`create_document`, `list_documents(profile_id)`, `get_document`, `delete_document`
(cascades chunks), `insert_chunks`, `iter_chunk_vectors(profile_id)`, `kb_stats`.

### 1.6 API — `backend/app/api/v1/endpoints/knowledge.py` (new)

Mount under `/v1/knowledge` in `router.py`. Profile via `X-Profile-ID` header
(`_profile` helper, same as templates). Schemas in `backend/app/schemas/knowledge.py`.

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/v1/knowledge/documents` | list KB docs for profile (id, filename, status, chunk_count) |
| `POST` | `/v1/knowledge/documents` | `multipart/form-data` upload → ingest (PDF/TXT/DOCX/MD, size cap e.g. 20 MB) |
| `DELETE` | `/v1/knowledge/documents/{id}` | delete doc + chunks |
| `POST` | `/v1/knowledge/search` | `{query, top_k}` → ranked chunks (debug / "test retrieval" UI) |

### 1.7 Retrieval injection into chat — `backend/app/services/chat_service.py`

- Extend `ChatCompletionRequest` (schemas/chat.py) with optional
  `rag: bool = False` (and optional `rag_top_k`).
- When `rag` is true: take the last user message text, call `rag_service.retrieve`,
  and inject a synthetic `system` (or context) message before the model call:
  *"Use the following retrieved context to answer. If irrelevant, ignore it.\n\n[chunks]"*.
  Applied in both `complete()` and `stream()` paths (and inside the tool loop's first
  turn). Emit an SSE `rag_context` control frame (sources used) so the UI can show
  citations — same `_sse_event` mechanism already used for tool frames.
- Chat endpoint needs `db` access for retrieval: add `Depends(get_db)` to the
  completions route and thread the connection into `ChatService` (currently DB-less),
  or open a short-lived connection inside the service. Prefer passing `db` through.

### 1.8 Frontend (Angular)

- `core/services/knowledge.service.ts` — list/upload/delete/search against `/v1/knowledge`.
- New sidebar panel **Knowledge base** in the chat feature (mirrors the templates/tags
  panels): upload (click + drag-drop, reuse existing drop overlay), document list with
  status badge + delete, and a **RAG toggle** (like the existing Tools ON/OFF toggle)
  that sets `rag: true` on outgoing completion requests.
- Render `rag_context` sources under the assistant bubble as small citation chips.
- New service field stored in `user-preferences.service.ts` (rag on/off persisted).

### 1.9 Tests

- `backend/tests/test_rag.py` — `chunk_text` boundaries/overlap; cosine ranking with a
  stubbed embedder (monkeypatch `embed_texts`); `extract_text` per format; ingest→retrieve
  round-trip against a temp SQLite DB.

---

## 2. Telegram scheduled reminders

`/remind 18:00 Check backups` (also accept `HH:MM` today/next-day and simple
`+30m` / `2h` relative forms).

- **Scheduler**: use PTB `JobQueue` (`application.job_queue.run_once`). Requires the
  `[job-queue]` extra → change `python-telegram-bot==21.5` to
  `python-telegram-bot[job-queue]==21.5` in `requirements.txt` (pulls APScheduler).
- **Persistence**: new `telegram_reminders` table (id, telegram_id, chat_id, text,
  fire_at, created_at, optional `llm_context` flag) + `telegram_reminder_repository.py`,
  so reminders survive a bot restart. On startup, reload pending reminders and
  re-schedule those with `fire_at > now`.
- **Handlers** in `backend/app/telegram/bot.py`:
  - `cmd_remind` — parse time + text, persist, schedule job, confirm.
  - `cmd_reminders` — list pending; `cmd_unremind <id>` — cancel.
  - Job callback sends the message; if the optional LLM-context flag is set, run a short
    non-streaming completion ("expand this reminder with helpful context") before sending.
  - Register the new `CommandHandler`s alongside the existing block (~line 1195).
- Localize confirmation/listing strings via the i18n helper from section 3.

---

## 3. Telegram multi-language support

`/lang en|it|...` switches the bot UI language per chat.

- **i18n module** — `backend/app/telegram/i18n.py`: nested dicts
  `MESSAGES[locale][key]` for `it` and `en` (default `it`, matching current hard-coded
  Italian strings), plus `t(locale, key, **kwargs)` with fallback to `en`/key.
- **Per-chat locale storage**: lightweight `telegram_prefs` table
  (`telegram_id PRIMARY KEY, locale, updated_at`) + small repository, with an in-memory
  cache (the bot already keeps per-chat in-memory session state).
- **Handlers**:
  - `cmd_lang` — no arg shows an inline keyboard of supported locales; arg sets directly.
  - A `_locale(update)` helper resolves the caller's locale for every handler.
- **Migration of existing strings**: replace user-facing literals in `bot.py`
  (`/start` help text, quick-action labels, status messages, reminder strings) with
  `t(locale, ...)` lookups. Do this incrementally — start with command replies and the
  `/start` help, since that block is the most visible.
- Optionally set localized command descriptions via `set_my_commands(scope=chat)`.

---

## Suggested PR sequence

1. **PR A — RAG backend**: schema, `numpy`, embedding_service, rag_service,
   kb_repository, `/v1/knowledge` endpoints, chat injection, tests.
2. **PR B — RAG frontend**: knowledge service + sidebar panel + RAG toggle + citations.
3. **PR C — Telegram reminders**: job-queue extra, reminders table/repo, commands.
4. **PR D — Telegram i18n**: i18n module, prefs table, `/lang`, string migration.

## Touched / new files (summary)

**New (backend):** `services/embedding_service.py`, `services/rag_service.py`,
`db/kb_repository.py`, `db/telegram_reminder_repository.py`, `db/telegram_prefs_repository.py`,
`api/v1/endpoints/knowledge.py`, `schemas/knowledge.py`, `telegram/i18n.py`, `tests/test_rag.py`.

**Modified (backend):** `db/database.py` (schema), `core/config.py` (`embedding_chain`),
`api/v1/router.py`, `schemas/chat.py` (`rag` flag), `services/chat_service.py`,
`api/v1/endpoints/chat.py` (db dep), `telegram/bot.py`, `requirements.txt`
(`numpy`, `python-telegram-bot[job-queue]`).

**New (frontend):** `core/services/knowledge.service.ts`, KB sidebar panel.
**Modified (frontend):** chat page component/template/styles, `user-preferences.service.ts`,
chat request payload (rag flag), citation rendering.

**Docs:** update `docs/roadmap.md` (Phase 14 → ✓ when done), `CHANGELOG.md`, `README.md`.

## Risks / notes

- Embeddings require at least one configured embed provider; Ollama local is the
  zero-cost default. Surface a clear error if no embed provider is available.
- Cosine over a full per-profile scan is O(n) — fine for hundreds/thousands of chunks;
  sqlite-vec is the documented upgrade path if corpora grow large.
- The chat completions endpoint currently has no DB dependency; adding one is the main
  cross-cutting change for RAG injection — keep it optional (only when `rag=true`).
