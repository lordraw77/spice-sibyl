"""Helpers shared by every graph-workflow sub-router.

Kept here (and not in one of the route modules) so the sub-routers stay
siblings: none of them has to import another to reuse a helper.
"""

import aiosqlite
from fastapi import HTTPException, Request

from app.db import graph_workflow_repository as repo
from app.schemas.graph_workflows import GraphWorkflowOut


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _owned(db: aiosqlite.Connection, wf_id: str, profile_id: str) -> GraphWorkflowOut:
    wf = await repo.get_workflow(db, wf_id)
    if not wf or wf.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf
