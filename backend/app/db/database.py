import logging

import aiosqlite
from app.core.config import settings

logger = logging.getLogger(__name__)

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

        await _bootstrap_admin(db)


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
    try:
        yield db
    finally:
        await db.close()
