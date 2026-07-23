"""
Shared, dependency-free helpers for workflow node handlers.

These live outside the engine module (``services/workflow_graph_service.py``)
so extracted node families under ``app/workflow/nodes/*`` can import them
without importing — and cycling with — the engine. Keep this module free of any
engine / db / provider imports: only stdlib and pure coercion utilities.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings

# ── shared engine/node constants ─────────────────────────────────────────────
# Pure limits referenced by both the engine core and several node families.
# Kept here (not in the engine module) so node modules can import them without
# importing the engine.
TOOL_RESULT_MAX_CHARS = 12000
MAX_LOOP_ITERATIONS = 1000
MAX_SUBWORKFLOW_DEPTH = 5
HTTP_MAX_TIMEOUT = 120.0
WAIT_MAX_SECONDS = 3600.0
DB_QUERY_MAX_ROWS = 1000            # rows returned by db.query, hard cap
FILE_MAX_BYTES = 10 * 1024 * 1024   # file.read/file.write size guard

# Underscore aliases for the engine's historical names.
_TOOL_RESULT_MAX_CHARS = TOOL_RESULT_MAX_CHARS
_MAX_LOOP_ITERATIONS = MAX_LOOP_ITERATIONS
_MAX_SUBWORKFLOW_DEPTH = MAX_SUBWORKFLOW_DEPTH
_HTTP_MAX_TIMEOUT = HTTP_MAX_TIMEOUT
_WAIT_MAX_SECONDS = WAIT_MAX_SECONDS
_DB_QUERY_MAX_ROWS = DB_QUERY_MAX_ROWS
_FILE_MAX_BYTES = FILE_MAX_BYTES


def as_bool(value) -> bool:
    """Coerce loosely-typed workflow values to bool.

    Strings ``true/1/yes/on`` (case-insensitive) are truthy; numbers use ``!= 0``;
    everything else falls back to ``bool()``. Shared by the ``if``/``filter``
    nodes and several engine handlers that read boolean params.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def safe_workspace_path(raw, *, create_dirs: bool = False) -> Path:
    """Resolve a node-supplied path INSIDE the workspace storage root
    (``GRAPH_WORKFLOW_FILES_DIR``). Absolute paths and ``..`` traversal that
    escape the root are rejected — file/db nodes can never touch the host FS.

    Shared by the io/messaging node families and the engine's file.watch poll
    loop, so it lives here rather than in either module.
    """
    rel = str(raw or "").strip()
    if not rel:
        raise ValueError("'path' is required")
    root = Path(settings.graph_workflow_files_dir).resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path {rel!r} escapes the workspace storage")
    if create_dirs:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


# Backwards-compatible private aliases: the engine historically referenced these
# with a leading underscore. Re-exported so existing call sites keep working.
_as_bool = as_bool
_safe_workspace_path = safe_workspace_path
