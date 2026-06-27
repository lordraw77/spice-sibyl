"""
SQLite backup / restore + per-profile export / import (Phase 16).

Backups use SQLite's online backup API (``sqlite3.Connection.backup``) which
produces a transactionally-consistent snapshot even with WAL active, without
blocking writers.  Per-profile export bundles a profile's conversations,
messages, knowledge base, templates and tags into a single JSON archive (zip)
for portability between instances.

All blocking sqlite3 work runs in a thread via ``asyncio.to_thread`` so the event
loop stays responsive.
"""

import asyncio
import io
import json
import logging
import re
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_BACKUP_PREFIX = "spice_sibyl-"
_BACKUP_SUFFIX = ".db"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Tables exported per profile, with the column used to scope rows. Order matters
# for import: parents before children (FK dependencies).
_PROFILE_TABLES = (
    ("conversations", "profile_id"),
    ("prompt_templates", "profile_id"),
    ("tags", "profile_id"),
    ("kb_documents", "profile_id"),
    ("kb_chunks", "profile_id"),
)


def _backup_dir() -> Path:
    path = Path(settings.backup_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── DB snapshot / restore ────────────────────────────────────────────────────

def _do_backup(dest: Path) -> None:
    src = sqlite3.connect(settings.db_path)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


async def make_backup() -> Path:
    """Create a timestamped snapshot of the DB in backup_dir; return its path."""
    # Millisecond precision avoids collisions when triggered twice in one second.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    dest = _backup_dir() / f"{_BACKUP_PREFIX}{stamp}{_BACKUP_SUFFIX}"
    await asyncio.to_thread(_do_backup, dest)
    logger.info("DB backup written to %s", dest)
    return dest


def list_backups() -> list[dict]:
    """Return available snapshots (newest first) with size + mtime."""
    out = []
    for f in sorted(_backup_dir().glob(f"{_BACKUP_PREFIX}*{_BACKUP_SUFFIX}"), reverse=True):
        stat = f.stat()
        out.append({"name": f.name, "size_bytes": stat.st_size, "created_at": int(stat.st_mtime)})
    return out


def prune(retention: int | None = None) -> int:
    """Delete all but the newest ``retention`` snapshots; return count removed."""
    keep = retention if retention is not None else settings.backup_retention
    files = sorted(_backup_dir().glob(f"{_BACKUP_PREFIX}*{_BACKUP_SUFFIX}"), reverse=True)
    removed = 0
    for f in files[keep:]:
        try:
            f.unlink()
            removed += 1
        except OSError:
            logger.warning("Could not delete old backup %s", f)
    return removed


def _resolve_backup(name: str) -> Path:
    """Resolve a backup name to a path inside backup_dir, rejecting traversal."""
    if "/" in name or "\\" in name or not name.startswith(_BACKUP_PREFIX):
        raise ValueError("Invalid backup name")
    path = (_backup_dir() / name).resolve()
    if path.parent != _backup_dir().resolve() or not path.is_file():
        raise ValueError("Backup not found")
    return path


def _do_restore(snapshot: Path) -> None:
    src = sqlite3.connect(str(snapshot))
    try:
        dst = sqlite3.connect(settings.db_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


async def restore_backup(name: str) -> None:
    """Overwrite the live DB with a stored snapshot.

    Should be run during a maintenance window: open connections keep their old
    view until reopened, so a process restart afterwards is recommended.
    """
    snapshot = _resolve_backup(name)
    await asyncio.to_thread(_do_restore, snapshot)
    logger.warning("DB restored from snapshot %s — restart recommended", name)


# ── Per-profile export / import ──────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _do_export(profile_id: str) -> bytes:
    conn = _conn()
    try:
        data: dict[str, object] = {"profile_id": profile_id, "exported_at": int(time.time()), "tables": {}}
        tables: dict[str, list[dict]] = {}

        for table, col in _PROFILE_TABLES:
            # table/col come from the fixed _PROFILE_TABLES allowlist, not user input
            rows = conn.execute(  # noqa: S608 — identifiers are constant; value is parameterized
                f"SELECT * FROM {table} WHERE {col} = ?", (profile_id,)
            ).fetchall()
            tables[table] = [_row_to_jsonable(r) for r in rows]

        # messages are scoped via their conversation, not profile directly
        conv_ids = [r["id"] for r in tables["conversations"]]
        messages: list[dict] = []
        conv_tags: list[dict] = []
        if conv_ids:
            placeholders = ",".join("?" * len(conv_ids))
            messages = [
                _row_to_jsonable(r)
                for r in conn.execute(  # noqa: S608 — placeholders are parameterized ?
                    f"SELECT * FROM messages WHERE conversation_id IN ({placeholders})", conv_ids
                ).fetchall()
            ]
            conv_tags = [
                _row_to_jsonable(r)
                for r in conn.execute(  # noqa: S608 — placeholders are parameterized ?
                    f"SELECT * FROM conversation_tags WHERE conversation_id IN ({placeholders})", conv_ids
                ).fetchall()
            ]
        tables["messages"] = messages
        tables["conversation_tags"] = conv_tags
        data["tables"] = tables

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("profile.json", json.dumps(data))
        return buf.getvalue()
    finally:
        conn.close()


def _row_to_jsonable(row: sqlite3.Row) -> dict:
    out: dict[str, object] = {}
    for key in row.keys():
        val = row[key]
        if isinstance(val, (bytes, bytearray)):
            out[key] = {"__blob__": val.hex()}
        else:
            out[key] = val
    return out


def _jsonable_to_value(val):
    if isinstance(val, dict) and "__blob__" in val:
        return bytes.fromhex(val["__blob__"])
    return val


def _do_import(archive: bytes, target_profile_id: str) -> dict:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        payload = json.loads(zf.read("profile.json"))

    tables: dict[str, list[dict]] = payload.get("tables", {})
    conn = _conn()
    counts: dict[str, int] = {}
    try:
        # Insert order respects FK dependencies.
        order = [
            "conversations", "messages", "prompt_templates",
            "tags", "conversation_tags", "kb_documents", "kb_chunks",
        ]
        for table in order:
            rows = tables.get(table, [])
            for row in rows:
                row = dict(row)
                # Re-home profile-scoped rows onto the target profile.
                if "profile_id" in row:
                    row["profile_id"] = target_profile_id
                cols = list(row.keys())
                # Column names are interpolated into SQL — reject anything that is
                # not a plain identifier so a crafted archive can't inject.
                if not all(_IDENTIFIER.match(c) for c in cols):
                    raise ValueError(f"Invalid column name in archive table {table}")
                placeholders = ",".join("?" * len(cols))
                values = [_jsonable_to_value(row[c]) for c in cols]
                conn.execute(  # noqa: S608 — table from allowlist; values parameterized
                    f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                    values,
                )
            counts[table] = len(rows)
        conn.commit()
    finally:
        conn.close()
    return counts


async def export_profile(profile_id: str) -> bytes:
    """Return a zip archive of the profile's data."""
    return await asyncio.to_thread(_do_export, profile_id)


async def import_profile(archive: bytes, target_profile_id: str) -> dict:
    """Import a profile archive into target_profile_id; return per-table counts."""
    return await asyncio.to_thread(_do_import, archive, target_profile_id)


# ── Scheduled loop ───────────────────────────────────────────────────────────

async def backup_loop() -> None:
    """Periodically snapshot the DB and prune old files. Cancelled on shutdown."""
    interval = max(1, settings.backup_interval_hours) * 3600
    logger.info("Scheduled DB backup enabled: every %dh, retention %d",
                settings.backup_interval_hours, settings.backup_retention)
    while True:
        try:
            await make_backup()
            prune()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled backup failed")
        await asyncio.sleep(interval)
