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

-- Phase 29: visual node-graph workflow engine (n8n-style). Coexists with the
-- Phase 18 agent runs above; the agent loop becomes one node type (llm.agent).
-- `graph_json` ({nodes, edges}) is the source of truth for a workflow; each
-- activation snapshots an immutable row into workflow_versions.
CREATE TABLE IF NOT EXISTS workflows (
    id             TEXT    PRIMARY KEY,
    profile_id     TEXT    NOT NULL DEFAULT 'default',
    name           TEXT    NOT NULL,
    description    TEXT    NOT NULL DEFAULT '',
    graph_json     TEXT    NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    variables_json TEXT    NOT NULL DEFAULT '{}',
    -- Phase 33 (roadmap fase 2.3): runs beyond this many simultaneously active
    -- go to status 'queued' and start when a slot frees. 0 = unlimited.
    max_concurrent_runs INTEGER NOT NULL DEFAULT 0,
    -- Phase 39 (roadmap fase 7.2): named environments — {name: {vars, secrets,
    -- version}}. `vars` overlay the workflow $vars, `secrets` remap $secrets
    -- aliases to real secret names, `version` pins the graph version runs in
    -- that environment execute ("promote to prod").
    environments_json TEXT NOT NULL DEFAULT '{}',
    -- Phase 41 (roadmap fase 9.1): when 1 (and the workflow is active with an
    -- input contract) it is published as a callable tool — to llm.agent, other
    -- workflows' tool.* nodes, the product chat and the MCP server.
    expose_as_tool INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 0,
    version        INTEGER NOT NULL DEFAULT 1,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workflows_profile ON workflows(profile_id, updated_at DESC);

-- Phase 41 (roadmap fase 9.3): conversation state for `chat`-triggered
-- workflows. One row per (workflow, session_id); `history_json` holds the
-- rolling [{role, content}] turns fed to the workflow as $trigger.history.
CREATE TABLE IF NOT EXISTS workflow_chat_sessions (
    id            TEXT    PRIMARY KEY,
    session_id    TEXT    NOT NULL,
    workflow_id   TEXT    NOT NULL,
    profile_id    TEXT    NOT NULL DEFAULT 'default',
    history_json  TEXT    NOT NULL DEFAULT '[]',
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_chat_sessions_key ON workflow_chat_sessions(workflow_id, session_id);
CREATE INDEX IF NOT EXISTS idx_workflow_chat_sessions_updated ON workflow_chat_sessions(updated_at);

-- Phase 32 (roadmap fase 1): profile-scoped workflow secrets, Fernet-encrypted
-- at rest (VAULT_SECRET_KEY). Exposed to expressions as $secrets.<name>; the
-- plaintext is never returned by the API nor persisted in run contexts.
CREATE TABLE IF NOT EXISTS workflow_secrets (
    id              TEXT    PRIMARY KEY,
    profile_id      TEXT    NOT NULL DEFAULT 'default',
    name            TEXT    NOT NULL,
    value_encrypted TEXT    NOT NULL,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_secrets_profile_name ON workflow_secrets(profile_id, name);

-- Immutable version history: one row per saved graph revision.
CREATE TABLE IF NOT EXISTS workflow_versions (
    id          TEXT    PRIMARY KEY,
    workflow_id TEXT    NOT NULL,
    version     INTEGER NOT NULL,
    graph_json  TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workflow_versions_wf ON workflow_versions(workflow_id, version DESC);

-- One execution of a workflow graph (evolves agent_runs). `context_json` holds
-- the resolvable run context (per-node outputs) so a run can be inspected/resumed.
CREATE TABLE IF NOT EXISTS workflow_runs (
    id           TEXT    PRIMARY KEY,
    workflow_id  TEXT    NOT NULL,
    profile_id   TEXT    NOT NULL DEFAULT 'default',
    status       TEXT    NOT NULL DEFAULT 'pending',  -- queued|pending|running|waiting|paused|completed|failed|cancelled
    trigger_type TEXT    NOT NULL DEFAULT 'manual',    -- manual|schedule|webhook|event
    graph_json   TEXT    NOT NULL,                     -- snapshot of the graph executed
    context_json TEXT,                                 -- JSON: {node_id: output, $trigger: ...}
    environment  TEXT,                                 -- Phase 39 (fase 7.2): environment the run executed in
    origin_run_id TEXT,                                -- Phase 39 (fase 7.1): run this one was retried/replayed from
    debug_json   TEXT,                                 -- Phase 40 (fase 8.3): step-debug state {breakpoints, pending_node, input}
    error        TEXT,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_wf ON workflow_runs(workflow_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_profile ON workflow_runs(profile_id, created_at DESC);

-- Per-node execution record within a run (evolves agent_run_steps).
CREATE TABLE IF NOT EXISTS workflow_node_runs (
    id          TEXT    PRIMARY KEY,
    run_id      TEXT    NOT NULL,
    node_id     TEXT    NOT NULL,
    node_type   TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',    -- pending|running|ok|error|skipped
    input_json  TEXT,
    output_json TEXT,
    error       TEXT,
    started_at  INTEGER,
    finished_at INTEGER,
    FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workflow_node_runs_run ON workflow_node_runs(run_id, started_at);

-- Triggers attached to a workflow: schedule (cron/RRULE/NL), webhook (token),
-- event (notification_events subscription). `next_run_at` drives the schedule poll.
CREATE TABLE IF NOT EXISTS workflow_triggers (
    id          TEXT    PRIMARY KEY,
    workflow_id TEXT    NOT NULL,
    type        TEXT    NOT NULL,                      -- manual|schedule|webhook|event
    config_json TEXT    NOT NULL DEFAULT '{}',
    token       TEXT,                                  -- webhook: public URL token
    next_run_at INTEGER,                               -- schedule: next fire time
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    fail_count  INTEGER NOT NULL DEFAULT 0,             -- Phase 30.b: consecutive firing failures
    last_error  TEXT,                                   -- Phase 30.b: most recent firing error
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workflow_triggers_wf ON workflow_triggers(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_triggers_sched ON workflow_triggers(type, enabled, next_run_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_triggers_token ON workflow_triggers(token);

-- Phase 35 (roadmap fase 4.4): human-in-the-loop approval requests. One row per
-- suspended `human.approval` node; the run stays in status 'waiting' until the
-- request is decided via POST /approvals/{id}/decision or `timeout_at` passes.
CREATE TABLE IF NOT EXISTS workflow_approvals (
    id          TEXT    PRIMARY KEY,
    run_id      TEXT    NOT NULL,
    node_id     TEXT    NOT NULL,
    workflow_id TEXT    NOT NULL,
    profile_id  TEXT    NOT NULL DEFAULT 'default',
    title       TEXT    NOT NULL DEFAULT '',
    message     TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'pending',    -- pending|approved|rejected|expired|cancelled|submitted|delivered
    timeout_at  INTEGER,                               -- unix ts after which the request expires
    comment     TEXT,                                  -- optional note left by the decider
    decided_by  TEXT,                                  -- user id that took the decision
    created_at  INTEGER NOT NULL,
    decided_at  INTEGER,
    FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workflow_approvals_run ON workflow_approvals(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_approvals_pending ON workflow_approvals(profile_id, status, created_at DESC);

-- Phase 43 (roadmap fase 11.1): saved regression test cases for a workflow —
-- a fixture $trigger payload plus assertions on chosen nodes' outputs. Run on
-- demand ("Run tests" in the editor toolbar); external nodes can be mocked
-- with their pinned output (fase 3.2) for deterministic results.
CREATE TABLE IF NOT EXISTS workflow_test_cases (
    id               TEXT    PRIMARY KEY,
    workflow_id      TEXT    NOT NULL,
    name             TEXT    NOT NULL,
    trigger_payload_json TEXT NOT NULL DEFAULT '{}',
    assertions_json  TEXT    NOT NULL DEFAULT '[]',
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workflow_test_cases_wf ON workflow_test_cases(workflow_id, created_at);

-- Phase 44 (roadmap fase 12.1): profile-wide ("workspace") LLM token and run
-- budget for the calendar month, on top of the per-workflow caps carried by
-- the workflows table itself. Usage is derived on the fly from workflow_runs /
-- workflow_node_runs (fase 5.1 stats) rather than duplicated here; only the
-- caps and the last period a soft-warning was already sent are persisted.
CREATE TABLE IF NOT EXISTS profile_budgets (
    profile_id           TEXT    PRIMARY KEY,
    token_budget_month   INTEGER,
    run_budget_month     INTEGER,
    warned_period        TEXT,
    updated_at           INTEGER NOT NULL
);

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

-- Phase 36 (roadmap fase 5.2): graph workflows shared into a workspace. Members
-- can inspect the shared definition and import a copy into their own profile
-- ($secrets never travel — references must be re-satisfied by the importer).
CREATE TABLE IF NOT EXISTS workspace_workflows (
    workspace_id TEXT    NOT NULL,
    workflow_id  TEXT    NOT NULL,
    shared_by    TEXT    NOT NULL,
    shared_at    INTEGER NOT NULL,
    -- Phase 39 (roadmap fase 7.3): what members may do with the shared workflow —
    -- viewer (inspect/import), editor (also launch runs), approver (also decide
    -- its human.approval requests). The owner keeps implicit admin rights.
    role         TEXT    NOT NULL DEFAULT 'viewer',
    PRIMARY KEY (workspace_id, workflow_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workspace_workflows_wf ON workspace_workflows(workflow_id);

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

-- Phase 46 (roadmap fase 14.1): remote runner registrations. An outbound-only
-- agent process registers once (issued a raw token, stored here only as its
-- hash) and heartbeats periodically; `status` flips to 'offline' by the caller
-- comparing `last_heartbeat_at` against GRAPH_WORKFLOW_RUNNER_HEARTBEAT_TIMEOUT
-- rather than a background sweep, so it is always accurate on read.
-- `allowed_node_types_json` empty = no restriction beyond the label match.
CREATE TABLE IF NOT EXISTS workflow_runners (
    id                       TEXT    PRIMARY KEY,
    profile_id               TEXT    NOT NULL DEFAULT 'default',
    name                     TEXT    NOT NULL,
    token_hash               TEXT    NOT NULL,
    labels_json              TEXT    NOT NULL DEFAULT '[]',
    allowed_node_types_json  TEXT    NOT NULL DEFAULT '[]',
    version                  TEXT,
    last_heartbeat_at        INTEGER,
    revoked                  INTEGER NOT NULL DEFAULT 0,
    created_at               INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runners_token ON workflow_runners(token_hash);
CREATE INDEX IF NOT EXISTS idx_workflow_runners_profile ON workflow_runners(profile_id);

-- Phase 46 (roadmap fase 14.1): one single-node execution job dispatched to a
-- runner. `payload_json` is the fase 3.1 test_node() contract — {node_type,
-- params (already expression-resolved, so any $secrets are inlined values,
-- never the vault), input} — the runner posts back {ok, output, handles,
-- logs} into `result_json`/`error`.
CREATE TABLE IF NOT EXISTS workflow_runner_jobs (
    id           TEXT    PRIMARY KEY,
    runner_id    TEXT    NOT NULL,
    run_id       TEXT,
    node_id      TEXT    NOT NULL,
    node_type    TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'queued',  -- queued|claimed|done|failed|timeout
    result_json  TEXT,
    error        TEXT,
    created_at   INTEGER NOT NULL,
    claimed_at   INTEGER,
    finished_at  INTEGER,
    FOREIGN KEY (runner_id) REFERENCES workflow_runners(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workflow_runner_jobs_runner ON workflow_runner_jobs(runner_id, status, created_at);

-- Phase 46 (roadmap fase 14.4): the `db` QueueDriver's backing store for
-- `queue.publish` / `queue.consume` — a persisted, at-least-once message
-- queue with no external broker. A real broker adapter (AMQP/Kafka/MQTT)
-- implements the same QueueDriver interface without touching this table.
CREATE TABLE IF NOT EXISTS workflow_queue_messages (
    id           TEXT    PRIMARY KEY,
    topic        TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    headers_json TEXT    NOT NULL DEFAULT '{}',
    status       TEXT    NOT NULL DEFAULT 'pending',  -- pending|consumed
    created_at   INTEGER NOT NULL,
    consumed_at  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_workflow_queue_messages_topic ON workflow_queue_messages(topic, status, created_at);

-- Phase 48 (roadmap fase 16.1): per-workflow persistent key/value state that
-- survives across runs (counters, cursors, "last processed id"). Values are
-- JSON; `expires_at` is an optional absolute-epoch TTL after which a key reads
-- as absent (lazy expiry + a periodic purge). Deliberately separate from the
-- workflow definition so it is never carried in an export.
CREATE TABLE IF NOT EXISTS workflow_state (
    workflow_id TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    value_json  TEXT    NOT NULL,
    expires_at  INTEGER,                                -- NULL = never expires
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (workflow_id, key),
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workflow_state_expiry ON workflow_state(expires_at);

-- Phase 48 (roadmap fase 16.2): trigger idempotency. A webhook/event trigger
-- with a `dedupKey` expression records the resolved key here on first delivery;
-- a repeat delivery of the same key within `dedupWindowSeconds` returns the
-- original run_id instead of starting a second run. Keys expire (TTL) so the
-- table stays bounded.
CREATE TABLE IF NOT EXISTS workflow_trigger_dedup (
    trigger_id  TEXT    NOT NULL,
    dedup_key   TEXT    NOT NULL,
    run_id      TEXT    NOT NULL,
    expires_at  INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (trigger_id, dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_workflow_trigger_dedup_expiry ON workflow_trigger_dedup(expires_at);

-- Phase 49 (roadmap fase 17.5): notification digest buffer. Each terminal run
-- of a workflow whose notify settings enable digest mode drops one row here
-- instead of an immediate message; the scheduler flushes due buffers per
-- (workflow, channel) into a single aggregated notification on the configured
-- interval. `error`/`waiting` events bypass this table and go out immediately.
CREATE TABLE IF NOT EXISTS workflow_notification_digest (
    id          TEXT    PRIMARY KEY,
    workflow_id TEXT    NOT NULL,
    profile_id  TEXT    NOT NULL DEFAULT 'default',
    channel     TEXT    NOT NULL DEFAULT 'inapp',        -- inapp|telegram (delivery target)
    outcome     TEXT    NOT NULL,                         -- completed|failed|cancelled
    run_id      TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workflow_notif_digest_wf ON workflow_notification_digest(workflow_id, channel, created_at);

-- Phase 51 (roadmap fase 19) — Custom Node SDK. One row per (profile, type,
-- version); the highest version of a type is the "current" one. `kind` is
-- `declarative` (a parameterised http.request template) or `python` (a module
-- run in the code sandbox). `manifest_json` holds the validated node.json,
-- `code` the module source for python nodes.
CREATE TABLE IF NOT EXISTS custom_nodes (
    id            TEXT    PRIMARY KEY,
    profile_id    TEXT    NOT NULL DEFAULT 'default',
    type          TEXT    NOT NULL,                    -- custom.<name>
    version       INTEGER NOT NULL DEFAULT 1,
    name          TEXT    NOT NULL,
    description    TEXT    NOT NULL DEFAULT '',
    category       TEXT    NOT NULL DEFAULT 'action',
    icon           TEXT    NOT NULL DEFAULT '',
    kind           TEXT    NOT NULL DEFAULT 'declarative', -- declarative|python
    manifest_json  TEXT    NOT NULL,
    code           TEXT,
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    UNIQUE (profile_id, type, version)
);
CREATE INDEX IF NOT EXISTS idx_custom_nodes_type ON custom_nodes(profile_id, type, version);

-- Phase 52 (roadmap fase 20.5) — Telegram command↔workflow bindings. A workflow
-- claims a bot command (`/report`); collisions per profile are rejected at save
-- time (UNIQUE). Used to register setMyCommands and route the command to a run.
CREATE TABLE IF NOT EXISTS telegram_command_bindings (
    id            TEXT    PRIMARY KEY,
    profile_id    TEXT    NOT NULL DEFAULT 'default',
    command       TEXT    NOT NULL,                    -- without the leading slash
    workflow_id   TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT '',
    created_at    INTEGER NOT NULL,
    UNIQUE (profile_id, command),
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tg_cmd_bindings_wf ON telegram_command_bindings(workflow_id);
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
    # Phase 30.b: trigger resilience — auto-disable after N consecutive firing failures
    "ALTER TABLE workflow_triggers ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE workflow_triggers ADD COLUMN last_error TEXT",
    # Phase 32 (roadmap fase 1): per-workflow variables exposed as $vars
    "ALTER TABLE workflows ADD COLUMN variables_json TEXT NOT NULL DEFAULT '{}'",
    # Phase 33 (roadmap fase 2.3): per-workflow run concurrency limit (0 = unlimited)
    "ALTER TABLE workflows ADD COLUMN max_concurrent_runs INTEGER NOT NULL DEFAULT 0",
    # Phase 38 (roadmap fase 6.4): optional JSON Schema contracts on the workflow —
    # input validated when a subworkflow node calls it, output on return.
    "ALTER TABLE workflows ADD COLUMN input_schema_json TEXT",
    "ALTER TABLE workflows ADD COLUMN output_schema_json TEXT",
    # Phase 39 (roadmap fase 7.2): named environments (vars/secrets bindings +
    # pinned version) on the workflow; runs record which environment they used.
    "ALTER TABLE workflows ADD COLUMN environments_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE workflow_runs ADD COLUMN environment TEXT",
    # Phase 39 (roadmap fase 7.1): retry/replay lineage — the run this one derives from.
    "ALTER TABLE workflow_runs ADD COLUMN origin_run_id TEXT",
    # Phase 39 (roadmap fase 7.3): per-share role on workflows shared into a workspace.
    "ALTER TABLE workspace_workflows ADD COLUMN role TEXT NOT NULL DEFAULT 'viewer'",
    # Phase 40 (roadmap fase 8.3): step-debug state on a run — {breakpoints:[...],
    # pending_node, input}. A run with debug state advances via POST /runs/{id}/debug.
    "ALTER TABLE workflow_runs ADD COLUMN debug_json TEXT",
    # Phase 41 (roadmap fase 9.1): tool-exposure flag on a workflow.
    "ALTER TABLE workflows ADD COLUMN expose_as_tool INTEGER NOT NULL DEFAULT 0",
    # Phase 42 (roadmap fase 10): human.input / wait.event generalise the Phase 35
    # approval row into a "waiting request" (kind: approval|input|event). schema_json
    # is the JSON Schema of a human.input form; data_json is the submitted form data
    # or the delivered wait.event payload; correlation_id is the wait.event key POST
    # /graph-workflows/events/{correlation_id} looks up.
    "ALTER TABLE workflow_approvals ADD COLUMN kind TEXT NOT NULL DEFAULT 'approval'",
    "ALTER TABLE workflow_approvals ADD COLUMN schema_json TEXT",
    "ALTER TABLE workflow_approvals ADD COLUMN data_json TEXT",
    "ALTER TABLE workflow_approvals ADD COLUMN correlation_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_workflow_approvals_correlation ON workflow_approvals(correlation_id)",
    # Phase 44 (roadmap fase 12.1): per-workflow LLM token / run caps for the
    # calendar month (NULL = unlimited) and the last period a soft-warning was
    # already sent (avoids re-notifying on every run once past the threshold).
    "ALTER TABLE workflows ADD COLUMN token_budget_month INTEGER",
    "ALTER TABLE workflows ADD COLUMN run_budget_month INTEGER",
    "ALTER TABLE workflows ADD COLUMN budget_warned_period TEXT",
    # Phase 44 (roadmap fase 12.2): per-workflow run/node-run retention override
    # in days (NULL = use the global GRAPH_WORKFLOW_RUNS_RETENTION_DAYS default).
    "ALTER TABLE workflows ADD COLUMN runs_retention_days INTEGER",
    # Phase 45 (roadmap fase 13.3): Git sync of definitions — every saved version
    # is committed as JSON to a configured repo ("workflow-as-code"); NULL
    # git_repo_url = sync disabled. git_token_secret names a $secrets entry
    # (never stored here); git_subpath is the path of the file inside the repo.
    "ALTER TABLE workflows ADD COLUMN git_repo_url TEXT",
    "ALTER TABLE workflows ADD COLUMN git_branch TEXT NOT NULL DEFAULT 'main'",
    "ALTER TABLE workflows ADD COLUMN git_token_secret TEXT",
    "ALTER TABLE workflows ADD COLUMN git_subpath TEXT",
    "ALTER TABLE workflows ADD COLUMN git_last_synced_at INTEGER",
    # Phase 46 (roadmap fase 14.3): per-run lease — the process instance id
    # currently executing this run and when that lease expires. Renewed on
    # every checkpoint while a run is active; a run whose lease has expired
    # (crash) is taken over by whichever instance next runs the startup resume
    # sweep, reusing the fase 2.4 checkpoint/resume mechanism. NULL/expired =
    # free to (re)claim — a no-op distinction in a single-process deployment.
    "ALTER TABLE workflow_runs ADD COLUMN lease_owner TEXT",
    "ALTER TABLE workflow_runs ADD COLUMN lease_expires_at INTEGER",
    # Phase 48 (roadmap fase 16.4): run priority. The per-workflow queue (fase
    # 2.3) and the scale-out dispatcher serve higher priority first, FIFO within
    # the same priority. 0 = normal; set from the trigger config or the launch API.
    "ALTER TABLE workflow_runs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
    # Phase 49 (roadmap fase 17) — scheduling, SLA and scale UX.
    # 17.1 — workflow-level blackout windows / holiday calendar: {windows: [{start,
    # end, days, tz}], skip_dates: ["YYYY-MM-DD"], on_conflict: skip|defer}. A
    # schedule due inside a window is skipped or deferred rather than run.
    "ALTER TABLE workflows ADD COLUMN blackout_json TEXT NOT NULL DEFAULT '{}'",
    # 17.2 — SLA monitor config: {max_duration_s, missed_grace_s, channels:[inapp,
    # telegram]}. A run exceeding max_duration_s, or a schedule overdue past
    # missed_grace_s, raises a one-time alert on the configured channels.
    "ALTER TABLE workflows ADD COLUMN sla_json TEXT NOT NULL DEFAULT '{}'",
    # 17.5 — per-workflow notification settings: {digest: {enabled, interval_s,
    # channel}}. When digest is on, terminal-run notifications are buffered and
    # sent as one periodic summary instead of one message per run.
    "ALTER TABLE workflows ADD COLUMN notify_json TEXT NOT NULL DEFAULT '{}'",
    # 17.3 — folders/tags/archive for the workflow navigator.
    "ALTER TABLE workflows ADD COLUMN folder TEXT",
    "ALTER TABLE workflows ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE workflows ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
    # 17.2 — dedup: a run raises its duration-SLA alert at most once.
    "ALTER TABLE workflow_runs ADD COLUMN sla_alerted INTEGER NOT NULL DEFAULT 0",
    # 17.2 — dedup: a schedule's missed-beat alert fires at most once per miss.
    "ALTER TABLE workflow_triggers ADD COLUMN last_sla_alert_at INTEGER",
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
