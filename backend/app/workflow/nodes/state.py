"""
Persistent per-workflow state nodes: state.get / state.set / state.increment
(Phase 48, roadmap fase 16.1).

Self-contained: depends only on ``settings`` and the graph-workflow repository
(the ``workflow_state`` table) — never the engine. Key/value state outlives a
single run and is never carried in an export.
"""

from __future__ import annotations

import aiosqlite

from app.core.config import settings
from app.db import graph_workflow_repository as repo
from app.workflow.registry import DispatchCtx, node


async def _exec_state(
    db: aiosqlite.Connection, workflow_id: str | None, ntype: str, params: dict, node_input,
) -> dict:
    """Fase 16.1 — per-workflow persistent key/value state that outlives a run.

    ``state.get`` → ``{key, value, found}``; ``state.set`` → ``{key, value}``;
    ``state.increment`` → ``{key, value}`` (numeric, atomic). ``ttlSeconds`` on
    set/increment gives the key an expiry (default from settings). Backed by the
    ``workflow_state`` table, never carried in an export."""
    if not workflow_id:
        raise ValueError("state.* nodes require a workflow context (not available in this run mode)")
    key = str(params.get("key") or "").strip()
    if not key:
        raise ValueError("state node requires a non-empty 'key'")

    if ntype == "state.get":
        found, value = await repo.state_get(db, workflow_id, key)
        if not found and "default" in params:
            value = params.get("default")
        return {"key": key, "value": value, "found": found}

    ttl = params.get("ttlSeconds")
    ttl = int(ttl) if isinstance(ttl, (int, float)) and not isinstance(ttl, bool) else None
    if ttl is None and settings.graph_workflow_state_default_ttl_seconds > 0:
        ttl = settings.graph_workflow_state_default_ttl_seconds

    if ntype == "state.set":
        # `value` defaults to the node's primary input, so `state.set` can park
        # whatever flowed in without an explicit expression.
        value = params["value"] if "value" in params else node_input
        await repo.state_set(db, workflow_id, key, value, ttl)
        return {"key": key, "value": value}

    # state.increment
    amount = params.get("amount", 1)
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        amount = 1
    value = await repo.state_increment(db, workflow_id, key, amount, ttl)
    return {"key": key, "value": value}


# -- persistent state (Phase 48, roadmap fase 16.1) --

@node("state.get", "state.set", "state.increment")
async def _h_state(c: DispatchCtx):
    return await _exec_state(c.db, c.ctx.get("_workflow_id"), c.ntype, c.params, c.node_input), ["main"]
