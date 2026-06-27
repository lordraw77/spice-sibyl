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
"""

_MIGRATIONS = [
    # Add profile_id to conversations if upgrading from an older DB
    "ALTER TABLE conversations ADD COLUMN profile_id TEXT NOT NULL DEFAULT 'default'",
    # Populate FTS index from existing messages (idempotent via INSERT OR IGNORE)
    "INSERT OR IGNORE INTO messages_fts(id, conversation_id, content) SELECT id, conversation_id, content FROM messages",
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
    # Backfill the chunk FTS index from existing chunks (idempotent via INSERT OR IGNORE)
    "INSERT OR IGNORE INTO kb_chunks_fts(id, document_id, profile_id, content) "
    "SELECT id, document_id, profile_id, content FROM kb_chunks",
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
