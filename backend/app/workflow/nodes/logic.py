"""
Control-flow & data-shaping nodes: set, if, switch, merge, filter, aggregate,
batch.

These are pure — no db, profile, provider or engine state — which makes them the
cleanest family to extract from the engine first (roadmap §4.1). Each handler
adapts ``DispatchCtx`` to a small implementation and returns
``(output, active_output_handles)``, identical to the former ``_dispatch``
branches. The engine re-imports the ``_exec_*`` helpers for its stateless
remote-runner path (``_dispatch_stateless``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.workflow.context import as_bool
from app.workflow.registry import DispatchCtx, node

if TYPE_CHECKING:
    from app.schemas.graph_workflows import GraphNode


# ── implementations ──────────────────────────────────────────────────────────

def _exec_switch(node: "GraphNode | None", params: dict, node_input) -> tuple[object, list[str]]:
    value = params.get("value")
    cases = params.get("cases")
    if not isinstance(cases, list):
        # cases may be declared on the node params as a list of match values.
        cases = []
    out = {"value": value, "input": node_input}
    for case in cases:
        if str(case) == str(value):
            return out, [f"case:{case}"]
    return out, ["default"]


def _exec_filter(params: dict, node_input) -> dict:
    items = params.get("items")
    if items is None:
        items = node_input if isinstance(node_input, list) else []
    keep = params.get("keep")
    if isinstance(keep, list):
        # keep is a same-length boolean mask resolved per item by the caller.
        filtered = [it for it, flag in zip(items, keep) if as_bool(flag)]
    else:
        filtered = list(items)
    return {"items": filtered, "count": len(filtered)}


def _resolve_field(item, field: str | None):
    if not field:
        return item
    value = item
    for part in str(field).split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _exec_aggregate(params: dict, node_input) -> dict:
    """Reduce an array (``items``, or the node input) with ``op`` over ``field``."""
    items = params.get("items")
    if items is None:
        items = node_input if isinstance(node_input, list) else []
    op = str(params.get("op") or "count").lower()
    field = params.get("field")

    if op == "count":
        return {"result": len(items), "count": len(items)}

    values = [_resolve_field(it, field) for it in items]
    values = [v for v in values if isinstance(v, (int, float))]

    if op == "concat":
        result = ", ".join(str(_resolve_field(it, field)) for it in items)
    elif not values:
        result = None
    elif op == "sum":
        result = sum(values)
    elif op == "avg":
        result = sum(values) / len(values)
    elif op == "min":
        result = min(values)
    elif op == "max":
        result = max(values)
    else:
        raise ValueError(f"aggregate: unknown op {op!r}")

    return {"result": result, "count": len(items)}


def _exec_batch(params: dict, node_input) -> dict:
    """Split an array (``items``, or the node input) into chunks of ``size``."""
    items = params.get("items")
    if items is None:
        items = node_input if isinstance(node_input, list) else []
    size = max(1, int(params.get("size") or 1))
    batches = [items[i:i + size] for i in range(0, len(items), size)]
    return {"batches": batches, "count": len(batches)}


# ── handlers ─────────────────────────────────────────────────────────────────

@node("set")
async def _h_set(c: DispatchCtx):
    fields = c.params.get("fields")
    return (fields if isinstance(fields, dict) else c.params), ["main"]


@node("if")
async def _h_if(c: DispatchCtx):
    cond = as_bool(c.params.get("condition"))
    return {"value": cond, "input": c.node_input}, ["true" if cond else "false"]


@node("switch")
async def _h_switch(c: DispatchCtx):
    return _exec_switch(c.node, c.params, c.node_input)


@node("merge")
async def _h_merge(c: DispatchCtx):
    items = c.node_input if isinstance(c.node_input, list) else [c.node_input]
    return {"items": items}, ["main"]


@node("filter")
async def _h_filter(c: DispatchCtx):
    return _exec_filter(c.params, c.node_input), ["main"]


@node("aggregate")
async def _h_aggregate(c: DispatchCtx):
    return _exec_aggregate(c.params, c.node_input), ["main"]


@node("batch")
async def _h_batch(c: DispatchCtx):
    return _exec_batch(c.params, c.node_input), ["main"]
