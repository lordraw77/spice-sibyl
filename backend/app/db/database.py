import aiosqlite
from app.core.config import settings

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS profiles (
    id         TEXT    PRIMARY KEY,
    name       TEXT    NOT NULL,
    created_at INTEGER NOT NULL
);

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
]


async def init_db() -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
        # Apply migrations idempotently (ignore errors = column already exists)
        for stmt in _MIGRATIONS:
            try:
                await db.execute(stmt)
                await db.commit()
            except Exception:
                pass


async def get_db():
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        await db.close()
