"""
Node dispatch registry — the table that replaces the workflow engine's giant
``_dispatch`` if/elif chain (roadmap §2.4 / §4.1).

Instead of ~40 ``if ntype == "…"`` branches, a node type maps to a handler in a
dict (O(1), exact match) or, for namespaced families like ``tool.`` /
``connector.`` / ``custom.``, a prefix list. Handlers are registered with the
``@node(...)`` decorator, so a node becomes independently testable and a new
node type is added by writing + registering one handler — no edit to a central
switch, no merge-conflict magnet.

Every handler has the same shape::

    async def handler(c: DispatchCtx) -> tuple[object, list[str]]

returning ``(output, active_output_handles)`` — identical to what each
``_dispatch`` branch returned. Cross-cutting concerns that ran *before* type
dispatch (remote ``runOn`` routing) stay in the engine and call ``resolve``
only for local execution, so behaviour is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:  # avoid import cycles / heavy imports at module load
    import aiosqlite

    from app.schemas.graph_workflows import GraphNode


@dataclass
class DispatchCtx:
    """Everything a node handler needs, carried as one object.

    Mirrors the old ``_dispatch(db, profile_id, node, node_input, params, ctx)``
    positional arguments so adapting a branch to a handler is mechanical.
    """

    db: "aiosqlite.Connection"
    profile_id: str
    node: "GraphNode"
    node_input: object
    params: dict
    ctx: dict

    @property
    def ntype(self) -> str:
        return self.node.type


Handler = Callable[[DispatchCtx], Awaitable[tuple[object, list[str]]]]

# Exact type → handler (e.g. "if", "http.request", "llm.agent").
NODE_HANDLERS: dict[str, Handler] = {}
# (prefix, handler) for namespaced families (e.g. "tool.", "connector.", "custom.").
# Order matters: the first matching prefix wins, so register from most to least
# specific if two prefixes could ever overlap (none do today).
PREFIX_HANDLERS: list[tuple[str, Handler]] = []


def node(*types: str, prefix: bool = False) -> Callable[[Handler], Handler]:
    """Register ``fn`` as the handler for one or more node types.

    ``@node("if")`` — exact match. ``@node("tool.", prefix=True)`` — matches any
    type starting with ``tool.``. A type may be registered once; re-registering
    the same exact type raises, catching accidental double-registration.
    """

    def deco(fn: Handler) -> Handler:
        for t in types:
            if prefix:
                PREFIX_HANDLERS.append((t, fn))
            else:
                if t in NODE_HANDLERS:
                    raise ValueError(f"node type already registered: {t}")
                NODE_HANDLERS[t] = fn
        return fn

    return deco


async def resolve(c: DispatchCtx) -> tuple[object, list[str]]:
    """Dispatch to the handler for ``c.node.type`` (exact, then prefix)."""
    handler = NODE_HANDLERS.get(c.ntype)
    if handler is not None:
        return await handler(c)
    for prefix, handler in PREFIX_HANDLERS:
        if c.ntype.startswith(prefix):
            return await handler(c)
    raise ValueError(f"unknown node type: {c.ntype}")
