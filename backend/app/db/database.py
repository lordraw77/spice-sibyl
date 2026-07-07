import logging

import aiosqlite
from app.core.config import settings

logger = logging.getLogger(__name__)

# wikillm: tri-state cache for sqlite-vec extension availability.
#   None  = not probed yet
#   True  = extension loaded, kb_chunk_vec usable (ANN KNN path)
#   False = unavailable/disabled → retrieval falls back to the numpy cosine scan
_vec_available: bool | None = None


async def _try_load_vec(db: aiosqlite.Connection) -> bool:
    """Load the sqlite-vec loadable extension on this connection.

    Returns True when vector functions/vec0 tables are usable. Memoises the
    outcome across connections and logs the fallback reason exactly once so a
    build without extension support (or rag_use_sqlite_vec=False) degrades to
    the numpy vector scan without spamming logs.
    """
    global _vec_available
    if not settings.rag_use_sqlite_vec:
        _vec_available = False
        return False
    try:
        import sqlite_vec

        await db.enable_load_extension(True)
        await db.load_extension(sqlite_vec.loadable_path())
        await db.enable_load_extension(False)
        _vec_available = True
        return True
    except Exception as exc:
        if _vec_available is None:
            logger.warning(
                "sqlite-vec unavailable (%s) — retrieval falls back to the numpy "
                "cosine scan; set rag_use_sqlite_vec=False to silence this.", exc,
            )
        _vec_available = False
        return False


def vec_available() -> bool:
    """Whether the sqlite-vec ANN path is usable (probed at init_db)."""
    return bool(_vec_available)


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS profiles (
    id         TEXT    PRIMARY KEY,
    name       TEXT    NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            TEXT    PRIMARY KEY,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',
    created_at    INTEGER NOT NULL,
    disabled      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    jti        TEXT    PRIMARY KEY,
    user_id    TEXT    NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id         TEXT    PRIMARY KEY,
    user_id    TEXT,
    action     TEXT    NOT NULL,
    resource   TEXT,
    detail     TEXT,
    ip         TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT    PRIMARY KEY,
    profile_id TEXT    NOT NULL DEFAULT 'default',
    title      TEXT    NOT NULL,
    model      TEXT    NOT NULL,
    channel    TEXT    NOT NULL DEFAULT 'web',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    provider_id   TEXT    PRIMARY KEY,
    encrypted_key TEXT    NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                  TEXT    PRIMARY KEY,
    conversation_id     TEXT    NOT NULL,
    role                TEXT    NOT NULL,
    content             TEXT    NOT NULL,
    model               TEXT,
    provider            TEXT,
    latency_ms          INTEGER,
    first_token_ms      INTEGER,
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    total_tokens        INTEGER,
    tokens_per_second   REAL,
    finish_reason       TEXT,
    estimated_cost      REAL,
    created_at          INTEGER NOT NULL,
    capabilities        TEXT,
    free                INTEGER,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_profile_id ON conversations(profile_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_provider ON messages(provider);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);

-- Phase 23.d: unified cross-channel reminders (replaces the Phase 14
-- telegram_reminders table — see _migrate_reminders below).
CREATE TABLE IF NOT EXISTS reminders (
    id               TEXT    PRIMARY KEY,
    owner_profile_id TEXT,
    chat_id          INTEGER,
    text             TEXT,
    smart_prompt     TEXT,
    recurrence       TEXT    NOT NULL DEFAULT 'once',
    fire_at          INTEGER NOT NULL,
    timezone         TEXT,
    channels         TEXT    NOT NULL DEFAULT 'telegram',
    active           INTEGER NOT NULL DEFAULT 1,
    fired            INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL,
    last_fired_at    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_reminders_fire ON reminders(fire_at);
CREATE INDEX IF NOT EXISTS idx_reminders_profile ON reminders(owner_profile_id);
CREATE INDEX IF NOT EXISTS idx_reminders_chat ON reminders(chat_id);

CREATE TABLE IF NOT EXISTS telegram_links (
    telegram_id INTEGER PRIMARY KEY,
    profile_id  TEXT    NOT NULL UNIQUE,
    username    TEXT,
    linked_at   INTEGER NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS prompt_templates (
    id         TEXT    PRIMARY KEY,
    profile_id TEXT    NOT NULL DEFAULT 'default',
    name       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id         TEXT    PRIMARY KEY,
    profile_id TEXT    NOT NULL DEFAULT 'default',
    name       TEXT    NOT NULL,
    color      TEXT    NOT NULL DEFAULT '#d6b279',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_tags (
    conversation_id TEXT NOT NULL,
    tag_id          TEXT NOT NULL,
    PRIMARY KEY (conversation_id, tag_id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shared_conversations (
    share_token     TEXT    PRIMARY KEY,
    conversation_id TEXT    NOT NULL,
    created_at      INTEGER NOT NULL,
    expires_at      INTEGER,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS telegram_prefs (
    chat_id    INTEGER PRIMARY KEY,
    locale     TEXT    NOT NULL DEFAULT 'it',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_reminders (
    id          TEXT    PRIMARY KEY,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER,
    text        TEXT    NOT NULL,
    fire_at     INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    fired       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_telegram_reminders_fire ON telegram_reminders(fire_at);
CREATE INDEX IF NOT EXISTS idx_telegram_reminders_chat ON telegram_reminders(chat_id);

CREATE TABLE IF NOT EXISTS kb_documents (
    id          TEXT    PRIMARY KEY,
    profile_id  TEXT    NOT NULL DEFAULT 'default',
    filename    TEXT    NOT NULL,
    mime        TEXT,
    size_bytes  INTEGER,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'pending',
    error       TEXT,
    -- Phase 17: web/URL ingestion + inline source highlighting
    source_type TEXT    NOT NULL DEFAULT 'file',   -- 'file' | 'url'
    source_url  TEXT,
    source_text TEXT,                              -- full extracted text (for deep-link highlighting / re-embed)
    content_hash TEXT,                             -- sha256 of the source bytes/text (duplicate detection)
    -- wikillm: canonical Markdown (MarkItDown output) + re-ingest flag
    markdown    TEXT,
    needs_reingest INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id           TEXT    PRIMARY KEY,
    document_id  TEXT    NOT NULL,
    profile_id   TEXT    NOT NULL DEFAULT 'default',
    chunk_index  INTEGER NOT NULL,
    content      TEXT    NOT NULL,
    -- Phase 17: character span of this chunk within kb_documents.source_text
    char_start   INTEGER NOT NULL DEFAULT 0,
    char_end     INTEGER NOT NULL DEFAULT 0,
    -- wikillm: Markdown section breadcrumb + heading this chunk belongs to
    section_path TEXT,
    heading      TEXT,
    embedding    BLOB,
    embed_model  TEXT,
    created_at   INTEGER NOT NULL,
    FOREIGN KEY (document_id) REFERENCES kb_documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_kb_documents_profile ON kb_documents(profile_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_profile ON kb_chunks(profile_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_document ON kb_chunks(document_id);

-- Phase 17: FTS5 lexical index over chunk text, powering hybrid (lexical+vector) retrieval.
-- Keyed by chunk id (UNINDEXED) like messages_fts; profile_id kept for scoped filtering.
CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_fts USING fts5(
    id           UNINDEXED,
    document_id  UNINDEXED,
    profile_id   UNINDEXED,
    content,
    tokenize     = 'unicode61'
);

CREATE TRIGGER IF NOT EXISTS kb_chunks_fts_ai
AFTER INSERT ON kb_chunks BEGIN
    INSERT INTO kb_chunks_fts(id, document_id, profile_id, content)
    VALUES (new.id, new.document_id, new.profile_id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS kb_chunks_fts_ad
AFTER DELETE ON kb_chunks BEGIN
    DELETE FROM kb_chunks_fts WHERE id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS kb_chunks_fts_au
AFTER UPDATE OF content ON kb_chunks BEGIN
    DELETE FROM kb_chunks_fts WHERE id = old.id;
    INSERT INTO kb_chunks_fts(id, document_id, profile_id, content)
    VALUES (new.id, new.document_id, new.profile_id, new.content);
END;

-- ── wikillm: MarkItDown-driven wiki + knowledge graph ──────────────────────────
-- Per-document section tree, built from Markdown headings (MarkItDown output).
CREATE TABLE IF NOT EXISTS kb_wiki_pages (
    id          TEXT    PRIMARY KEY,
    profile_id  TEXT    NOT NULL DEFAULT 'default',
    document_id TEXT    NOT NULL,
    parent_id   TEXT,                          -- nesting via heading level
    heading     TEXT    NOT NULL,
    level       INTEGER NOT NULL DEFAULT 1,
    char_start  INTEGER NOT NULL DEFAULT 0,    -- span within kb_documents.markdown
    char_end    INTEGER NOT NULL DEFAULT 0,
    summary     TEXT    NOT NULL DEFAULT '',    -- extractive in Phase 1, LLM later
    ord         INTEGER NOT NULL DEFAULT 0,     -- document order for stable rendering
    created_at  INTEGER NOT NULL,
    FOREIGN KEY (document_id) REFERENCES kb_documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_kb_wiki_profile ON kb_wiki_pages(profile_id);
CREATE INDEX IF NOT EXISTS idx_kb_wiki_document ON kb_wiki_pages(document_id);

-- Knowledge-graph nodes (document | section | entity). Entities dedupe per
-- (profile_id, type, norm_key) so the same name across documents is one node.
CREATE TABLE IF NOT EXISTS kb_graph_nodes (
    id          TEXT    PRIMARY KEY,
    profile_id  TEXT    NOT NULL DEFAULT 'default',
    type        TEXT    NOT NULL,               -- 'document' | 'section' | 'entity'
    norm_key    TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    document_id TEXT,                           -- owning doc (NULL = cross-doc entity)
    summary     TEXT    NOT NULL DEFAULT '',
    meta_json   TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_graph_nodes_profile ON kb_graph_nodes(profile_id);
CREATE INDEX IF NOT EXISTS idx_kb_graph_nodes_key ON kb_graph_nodes(profile_id, type, norm_key);
CREATE INDEX IF NOT EXISTS idx_kb_graph_nodes_document ON kb_graph_nodes(document_id);

-- Knowledge-graph edges (document→entity mentions, entity→entity related, …).
CREATE TABLE IF NOT EXISTS kb_graph_edges (
    id          TEXT    PRIMARY KEY,
    profile_id  TEXT    NOT NULL DEFAULT 'default',
    src_node_id TEXT    NOT NULL,
    dst_node_id TEXT    NOT NULL,
    relation    TEXT    NOT NULL,               -- 'contains' | 'mentions' | 'links_to' | 'related'
    weight      REAL    NOT NULL DEFAULT 1.0,
    meta_json   TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_graph_edges_profile ON kb_graph_edges(profile_id);
CREATE INDEX IF NOT EXISTS idx_kb_graph_edges_src ON kb_graph_edges(src_node_id);
CREATE INDEX IF NOT EXISTS idx_kb_graph_edges_dst ON kb_graph_edges(dst_node_id);

-- Chunk ↔ entity-node mentions: drives 1-hop graph expansion during retrieval
-- (seed chunk → shared entity → sibling chunks). Cascades with its chunk.
CREATE TABLE IF NOT EXISTS kb_chunk_entities (
    chunk_id   TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    profile_id TEXT NOT NULL DEFAULT 'default',
    PRIMARY KEY (chunk_id, node_id),
    FOREIGN KEY (chunk_id) REFERENCES kb_chunks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_kb_chunk_entities_node ON kb_chunk_entities(node_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunk_entities_profile ON kb_chunk_entities(profile_id);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    id              UNINDEXED,
    conversation_id UNINDEXED,
    content,
    tokenize        = 'unicode61'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_ai
AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(id, conversation_id, content)
    VALUES (new.id, new.conversation_id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_ad
AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_au
AFTER UPDATE OF content ON messages BEGIN
    DELETE FROM messages_fts WHERE id = old.id;
    INSERT INTO messages_fts(id, conversation_id, content)
    VALUES (new.id, new.conversation_id, new.content);
END;

-- Phase 18: MCP server registry. One row per stdio MCP server, stored in the
-- standard `mcpServers` config shape (command/args/env/... kept verbatim in
-- `config`). Global (admin-managed), not per-profile.
CREATE TABLE IF NOT EXISTS mcp_servers (
    id          TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    config      TEXT    NOT NULL,              -- JSON: {command, args, env, cwd, ...}
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

-- Phase 18: user-defined custom tools. HTTP-backed functions registered from
-- the UI, injected into the chat tool loop namespaced `custom__<name>`.
-- Per profile; name unique within a profile.
CREATE TABLE IF NOT EXISTS custom_tools (
    id          TEXT    PRIMARY KEY,
    profile_id  TEXT    NOT NULL DEFAULT 'default',
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    parameters  TEXT    NOT NULL,              -- JSON schema of the arguments
    endpoint    TEXT    NOT NULL,              -- JSON: {url, method, headers, auth, timeout}
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    UNIQUE (profile_id, name)
);

CREATE INDEX IF NOT EXISTS idx_custom_tools_profile ON custom_tools(profile_id);

-- Phase 18: persistent multi-step workflows (agent runs). The run's full
-- message history is serialized in `messages` after every iteration so a
-- paused/interrupted run can resume exactly where it stopped.
CREATE TABLE IF NOT EXISTS agent_runs (
    id           TEXT    PRIMARY KEY,
    profile_id   TEXT    NOT NULL DEFAULT 'default',
    goal         TEXT    NOT NULL,
    model        TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',  -- pending|running|paused|completed|failed|cancelled
    max_steps    INTEGER NOT NULL DEFAULT 20,
    current_step INTEGER NOT NULL DEFAULT 0,
    messages     TEXT,                                -- JSON: serialized conversation state
    result       TEXT,
    error        TEXT,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_profile ON agent_runs(profile_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_run_steps (
    id         TEXT    PRIMARY KEY,
    run_id     TEXT    NOT NULL,
    step_index INTEGER NOT NULL,
    kind       TEXT    NOT NULL,   -- assistant|tool_call|tool_result|final|error|note
    name       TEXT,               -- tool name for tool_call/tool_result
    content    TEXT    NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_run_steps_run ON agent_run_steps(run_id, step_index);

-- Phase 19: per-profile persistent memory. One row per remembered fact;
-- auto-extracted after each exchange (MEMORY_EXTRACTION_MODEL) or added
-- manually from the UI / Telegram. Injected into the system prompt when the
-- profile has memory enabled.
CREATE TABLE IF NOT EXISTS profile_memories (
    id                     TEXT    PRIMARY KEY,
    profile_id             TEXT    NOT NULL DEFAULT 'default',
    content                TEXT    NOT NULL,
    category               TEXT    NOT NULL DEFAULT 'fact',  -- preference|fact|project|instruction
    source_conversation_id TEXT,
    enabled                INTEGER NOT NULL DEFAULT 1,
    created_at             INTEGER NOT NULL,
    updated_at             INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_profile_memories_profile ON profile_memories(profile_id, updated_at DESC);

-- Phase 20.a: shared workspaces. A workspace is a team container owned by a
-- user; other users join as members with a role. Conversations and knowledge
-- base documents (owned by an individual profile) are *shared into* a workspace
-- via join tables, making them visible to every member per their role.
CREATE TABLE IF NOT EXISTS workspaces (
    id         TEXT    PRIMARY KEY,
    name       TEXT    NOT NULL,
    owner_id   TEXT    NOT NULL,              -- users.id
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT    NOT NULL,
    user_id      TEXT    NOT NULL,
    role         TEXT    NOT NULL DEFAULT 'viewer',  -- owner|admin|editor|viewer
    added_at     INTEGER NOT NULL,
    PRIMARY KEY (workspace_id, user_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id);

CREATE TABLE IF NOT EXISTS workspace_conversations (
    workspace_id    TEXT    NOT NULL,
    conversation_id TEXT    NOT NULL,
    shared_by       TEXT    NOT NULL,
    shared_at       INTEGER NOT NULL,
    PRIMARY KEY (workspace_id, conversation_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workspace_conversations_conv ON workspace_conversations(conversation_id);

CREATE TABLE IF NOT EXISTS workspace_documents (
    workspace_id TEXT    NOT NULL,
    document_id  TEXT    NOT NULL,
    shared_by    TEXT    NOT NULL,
    shared_at    INTEGER NOT NULL,
    PRIMARY KEY (workspace_id, document_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES kb_documents(id) ON DELETE CASCADE
);

-- Phase 20.b: threaded comments / annotations. A comment targets a
-- conversation (message_id NULL) or a specific message within it; threading is
-- via parent_id. Visible to anyone who can access the conversation (its owner
-- or a member of a workspace it is shared into). Soft-deleted so replies keep
-- their thread anchor.
CREATE TABLE IF NOT EXISTS comments (
    id              TEXT    PRIMARY KEY,
    conversation_id TEXT    NOT NULL,
    message_id      TEXT,                       -- NULL = conversation-level
    parent_id       TEXT,                       -- NULL = top-level thread
    user_id         TEXT    NOT NULL,
    body            TEXT    NOT NULL,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    deleted         INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES comments(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_comments_conversation ON comments(conversation_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_comments_message ON comments(message_id);

-- Phase 23: roaming preferences. A single JSON blob per owner, letting a user's
-- UI/appearance settings (owner_key 'user:<uid>') and each profile's chat
-- settings (owner_key 'profile:<pid>') follow the account across devices.
CREATE TABLE IF NOT EXISTS preferences (
    owner_key  TEXT    PRIMARY KEY,   -- 'user:<uid>' | 'profile:<pid>'
    data       TEXT    NOT NULL,      -- JSON blob, shape owned by the frontend
    updated_at INTEGER NOT NULL
);

-- Phase 23.c: cross-channel notification events (Telegram → web direction).
-- Persisted so a badge/unread count survives when no SSE stream is open;
-- also fanned out live to any connected stream via an in-memory queue.
CREATE TABLE IF NOT EXISTS notification_events (
    id         TEXT    PRIMARY KEY,
    user_id    TEXT    NOT NULL,
    profile_id TEXT    NOT NULL,
    event_type TEXT    NOT NULL,
    title      TEXT    NOT NULL,
    body       TEXT    NOT NULL DEFAULT '',
    meta_json  TEXT,
    created_at INTEGER NOT NULL,
    read       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_notification_events_user ON notification_events(user_id, created_at);
"""

_MIGRATIONS = [
    # Add profile_id to conversations if upgrading from an older DB
    "ALTER TABLE conversations ADD COLUMN profile_id TEXT NOT NULL DEFAULT 'default'",
    # Populate FTS index from existing messages. FTS5 tables have no UNIQUE
    # constraint so OR IGNORE can't dedupe — guard with NOT EXISTS so re-running
    # this migration on every boot doesn't duplicate rows.
    "INSERT INTO messages_fts(id, conversation_id, content) "
    "SELECT id, conversation_id, content FROM messages m "
    "WHERE NOT EXISTS (SELECT 1 FROM messages_fts f WHERE f.id = m.id)",
    # Phase 10: message pins
    "ALTER TABLE messages ADD COLUMN pinned INTEGER DEFAULT 0",
    # Phase 10: conversation branching
    "ALTER TABLE messages ADD COLUMN parent_id TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN branch_index INTEGER DEFAULT 0",
    # Phase 13: profiles belong to a user account (NULL = orphan, pre-auth profile)
    "ALTER TABLE profiles ADD COLUMN user_id TEXT DEFAULT NULL",
    # Phase 17: advanced RAG — URL ingestion, source highlighting, hybrid search
    "ALTER TABLE kb_documents ADD COLUMN source_type TEXT NOT NULL DEFAULT 'file'",
    "ALTER TABLE kb_documents ADD COLUMN source_url TEXT",
    "ALTER TABLE kb_documents ADD COLUMN source_text TEXT",
    "ALTER TABLE kb_chunks ADD COLUMN char_start INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE kb_chunks ADD COLUMN char_end INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE kb_documents ADD COLUMN content_hash TEXT",
    "CREATE INDEX IF NOT EXISTS idx_kb_documents_hash ON kb_documents(profile_id, content_hash)",
    # Backfill the chunk FTS index from existing chunks. Guarded with NOT EXISTS
    # (FTS5 has no UNIQUE constraint) so re-running on every boot is idempotent.
    "INSERT INTO kb_chunks_fts(id, document_id, profile_id, content) "
    "SELECT id, document_id, profile_id, content FROM kb_chunks c "
    "WHERE NOT EXISTS (SELECT 1 FROM kb_chunks_fts f WHERE f.id = c.id)",
    # Phase 19: per-profile memory toggle (OFF = no extraction, no injection)
    "ALTER TABLE profiles ADD COLUMN memory_enabled INTEGER NOT NULL DEFAULT 1",
    # Phase 19: message feedback (👍/👎 + optional note) on assistant messages
    "ALTER TABLE messages ADD COLUMN rating INTEGER DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN feedback_note TEXT DEFAULT NULL",
    # Phase 19: per-chat Telegram memory toggle (/memory on|off)
    "ALTER TABLE telegram_prefs ADD COLUMN memory INTEGER NOT NULL DEFAULT 1",
    # Phase 21: per-chat Telegram RAG toggle (/rag on|off) — OFF by default
    "ALTER TABLE telegram_prefs ADD COLUMN rag INTEGER NOT NULL DEFAULT 0",
    # Phase 22: per-profile UI locale (NULL = follow the browser language)
    "ALTER TABLE profiles ADD COLUMN locale TEXT DEFAULT NULL",
    # Phase 23.a: conversation channel of origin (web | telegram) for cross-channel history
    "ALTER TABLE conversations ADD COLUMN channel TEXT NOT NULL DEFAULT 'web'",
    # Phase 23.a: per-chat "active conversation" so Telegram exchanges persist as
    # regular profile conversations (linked users) instead of in-memory history
    "ALTER TABLE telegram_prefs ADD COLUMN active_conversation_id TEXT DEFAULT NULL",
    # Phase 23.b: per-chat Telegram tool-loop toggle (/tools on|off) — OFF by default
    "ALTER TABLE telegram_prefs ADD COLUMN tools INTEGER NOT NULL DEFAULT 0",
    # Phase 23.c: per-chat Telegram notification mute (/notify on|off) — ON by default
    "ALTER TABLE telegram_prefs ADD COLUMN notify INTEGER NOT NULL DEFAULT 1",
    # wikillm: canonical Markdown (MarkItDown output) + structural chunk metadata.
    "ALTER TABLE kb_documents ADD COLUMN markdown TEXT",
    "ALTER TABLE kb_documents ADD COLUMN needs_reingest INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE kb_chunks ADD COLUMN section_path TEXT",
    "ALTER TABLE kb_chunks ADD COLUMN heading TEXT",
    # wikillm clean-replacement: pre-wikillm documents have no Markdown, wiki pages
    # or graph nodes — flag them so the batch re-ingest can rebuild them. Idempotent:
    # once re-ingested `markdown` is populated and the row no longer matches.
    "UPDATE kb_documents SET needs_reingest = 1 WHERE markdown IS NULL",
]


async def init_db() -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row  # bootstrap reads rows by column name
        await db.executescript(_SCHEMA)
        await db.commit()
        for stmt in _MIGRATIONS:
            try:
                await db.execute(stmt)
                await db.commit()
            except aiosqlite.OperationalError as exc:
                # Expected for "column already exists" or "table already exists" —
                # migrations are intentionally idempotent.
                logger.debug("Migration skipped (already applied): %s", exc)
            except Exception:
                logger.exception("Unexpected migration error; stmt=%s", stmt)

        # wikillm: load sqlite-vec and materialise the ANN table. The vec0 virtual
        # table needs the extension loaded, so it lives here (not in _SCHEMA). Its
        # width is pinned to settings.embedding_dim with a cosine distance metric;
        # chunks whose embedding width differs are served by the numpy fallback.
        if await _try_load_vec(db):
            try:
                await db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunk_vec USING vec0("
                    "chunk_id TEXT PRIMARY KEY, "
                    "profile_id TEXT, "
                    "document_id TEXT, "
                    f"embedding float[{int(settings.embedding_dim)}] distance_metric=cosine)"
                )
                await db.commit()
            except Exception:
                logger.exception("Failed to create kb_chunk_vec; disabling sqlite-vec path")
                global _vec_available
                _vec_available = False

        await _migrate_reminders(db)
        await _bootstrap_admin(db)


async def _migrate_reminders(db: aiosqlite.Connection) -> None:
    """One-time migration: Phase 14 telegram_reminders -> Phase 23.d reminders.

    Copies every row (recurrence='once', channels='telegram'), best-effort
    resolving owner_profile_id via the chat's Telegram link, then drops the
    old table. Guarded by a table-existence check so it runs at most once.
    """
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='telegram_reminders'"
    ) as cursor:
        exists = await cursor.fetchone()
    if not exists:
        return

    from app.db import telegram_link_repository

    async with db.execute("SELECT * FROM telegram_reminders") as cursor:
        rows = await cursor.fetchall()

    for row in rows:
        link = await telegram_link_repository.get_by_telegram_id(db, row["chat_id"])
        owner_profile_id = link["profile_id"] if link else None
        await db.execute(
            "INSERT OR IGNORE INTO reminders "
            "(id, owner_profile_id, chat_id, text, recurrence, fire_at, channels, active, fired, created_at) "
            "VALUES (?, ?, ?, ?, 'once', ?, 'telegram', 1, ?, ?)",
            (row["id"], owner_profile_id, row["chat_id"], row["text"], row["fire_at"], row["fired"], row["created_at"]),
        )
    await db.execute("DROP TABLE telegram_reminders")
    await db.commit()
    logger.info("Migrated %d reminder row(s) from telegram_reminders to reminders", len(rows))


async def _bootstrap_admin(db: aiosqlite.Connection) -> None:
    """
    On an empty users table, create the bootstrap admin from ADMIN_EMAIL /
    ADMIN_PASSWORD and adopt every orphan (pre-auth) profile so existing data
    stays reachable.  Without this, mandatory auth on a fresh DB locks everyone out.
    """
    from app.db import user_repository
    from app.services import auth_service

    if await user_repository.count_users(db) > 0:
        return

    if not settings.admin_email or not settings.admin_password:
        logger.warning(
            "SECURITY: no users exist and ADMIN_EMAIL/ADMIN_PASSWORD are unset. "
            "With mandatory auth enabled, nobody can log in. Set both env vars and restart."
        )
        return

    admin = await user_repository.create_user(
        db,
        email=settings.admin_email,
        password_hash=auth_service.hash_password(settings.admin_password),
        role="admin",
    )
    await db.execute(
        "UPDATE profiles SET user_id = ? WHERE user_id IS NULL", (admin.id,)
    )
    await db.commit()
    logger.info("Bootstrapped admin user '%s' and adopted orphan profiles", admin.email)


async def get_db():
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    # wikillm: reload sqlite-vec per connection so vec0 KNN queries resolve. Skipped
    # cheaply when the extension was found unavailable at startup (numpy fallback).
    if _vec_available:
        await _try_load_vec(db)
    try:
        yield db
    finally:
        await db.close()
