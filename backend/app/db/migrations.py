"""Versioned schema migrations (roadmap v2 § 3, P1 "migrazioni versionate").

Before this module every ALTER TABLE lived in one list that ``init_db`` replayed
on **every** boot, swallowing the resulting OperationalError as the way of
telling "already applied" from "broken". That is idempotent but blind: a
genuinely broken statement looks exactly like an already-applied one, and the
cost grows with every release.

Now each migration is a numbered unit recorded in ``schema_migrations`` once it
succeeds, and boot only runs what is missing.

Two flavours:

* ``tolerant=True`` — the legacy baseline (version 1). It replays the historical
  statement list exactly as before, still swallowing OperationalError, because
  on a database created before this ledger existed we cannot know which of them
  already ran. It runs at most once per database and is then stamped.
* ``tolerant=False`` — everything added from now on. Failures propagate and the
  version is *not* recorded, so a bad deploy stops at boot instead of leaving a
  half-migrated schema behind.

Adding a migration: append a ``Migration`` with the next version number to
``MIGRATIONS``. Never edit or renumber one that has shipped — a database that
already recorded that version will not run it again.
"""

import logging
import time
from dataclasses import dataclass, field

import aiosqlite

logger = logging.getLogger(__name__)

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    applied_at INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class Migration:
    """One numbered, once-applied schema change."""

    version: int
    name: str
    statements: tuple[str, ...] = field(default_factory=tuple)
    #: Swallow OperationalError instead of failing. Only the legacy baseline
    #: sets this: for a fresh migration a failure is a bug, not a no-op.
    tolerant: bool = False


# ── version 1 — the pre-ledger statement list, verbatim ─────────────────────
#
# Historical order is load-bearing (later ALTERs assume earlier ones), so this
# list is kept exactly as it was in database.py. Do not append here: new work
# goes into its own Migration below.
_LEGACY_STATEMENTS = (
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
)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="legacy-baseline",
        statements=_LEGACY_STATEMENTS,
        tolerant=True,
    ),
    Migration(
        version=2,
        name="coordination-tables",
        statements=(
            # Leader election: one row per coordinated duty (the schedule poll
            # loop today). Whoever holds an unexpired lease is the leader.
            """
            CREATE TABLE IF NOT EXISTS instance_leases (
                name       TEXT    PRIMARY KEY,
                owner      TEXT    NOT NULL,
                expires_at INTEGER NOT NULL
            )
            """,
            # Shared sliding-window rate limiting, for the database backend.
            """
            CREATE TABLE IF NOT EXISTS rate_limit_hits (
                bucket TEXT NOT NULL,
                at     REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_hits ON rate_limit_hits(bucket, at)",
            # Cross-instance run events, for the database event bus.
            """
            CREATE TABLE IF NOT EXISTS run_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     TEXT    NOT NULL,
                payload    TEXT    NOT NULL,
                created_at INTEGER NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, id)",
        ),
    ),
)


async def applied_versions(db: aiosqlite.Connection) -> set[int]:
    """Versions already recorded in the ledger (empty set on a pre-ledger DB)."""
    await db.executescript(_LEDGER_DDL)
    async with db.execute("SELECT version FROM schema_migrations") as cursor:
        return {row[0] for row in await cursor.fetchall()}


async def pending(db: aiosqlite.Connection) -> list[Migration]:
    """Migrations this database has not recorded yet, in version order."""
    done = await applied_versions(db)
    return [m for m in sorted(MIGRATIONS, key=lambda m: m.version) if m.version not in done]


async def apply(db: aiosqlite.Connection) -> list[int]:
    """Run every pending migration and return the versions applied.

    Each migration commits and stamps itself, so an interrupted boot resumes
    where it stopped instead of replaying what already succeeded.
    """
    applied: list[int] = []
    for migration in await pending(db):
        for stmt in migration.statements:
            try:
                await db.execute(stmt)
            except aiosqlite.OperationalError as exc:
                if not migration.tolerant:
                    await db.rollback()
                    raise
                # Expected on the legacy baseline: "duplicate column name",
                # "table already exists" — the statement had already run on a
                # database created before the ledger existed.
                logger.debug("Migration %d skipped a statement: %s", migration.version, exc)
        await db.execute(
            "INSERT OR REPLACE INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (migration.version, migration.name, int(time.time())),
        )
        await db.commit()
        applied.append(migration.version)
        logger.info("Applied schema migration %d (%s)", migration.version, migration.name)
    return applied
