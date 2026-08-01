"""
Custom Node SDK family (Phase 51, roadmap fase 19): the ``custom.<name>``
prefix.

Thin adapter — the actual manifest lookup, template/python execution and
sandboxing live in ``app.services.custom_node_service``. Kept as its own tiny
module (rather than inline in the engine) so the ``custom.`` prefix registers
like every other family and the engine no longer owns node dispatch for it.
The service is imported lazily inside the handler so importing this family at
startup never drags the service (and its heavier deps) into the import graph.
"""

from __future__ import annotations

from app.workflow.registry import DispatchCtx, node


@node("custom.", prefix=True)
async def _h_custom(c: DispatchCtx):
    from app.services import custom_node_service

    return await custom_node_service.execute(
        c.db, c.profile_id, c.ntype, c.params, c.node_input, c.ctx
    )
