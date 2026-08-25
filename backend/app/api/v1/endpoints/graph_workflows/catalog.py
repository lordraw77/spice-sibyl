"""Node palette and shipped examples.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import aiosqlite
from fastapi import APIRouter, Depends

from app.data.node_catalog import node_catalog
from app.db.database import get_db
from app.dependencies.auth import resolve_profile
from app.examples import list_graph_workflow_examples
from app.schemas.graph_workflows import GraphWorkflowExample, NodeTypeInfo

router = APIRouter()

# ── palette ─────────────────────────────────────────────────────────────────

@router.get("/node-types", response_model=list[NodeTypeInfo])
async def node_types(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Palette catalog: static nodes + a ``tool.<name>`` node per built-in tool,
    plus discovered MCP server tools and the profile's custom tools."""
    return await node_catalog(db, profile_id)


@router.get("/examples", response_model=list[GraphWorkflowExample])
async def list_examples():
    """Curated, one-click-importable graph workflows. Static path declared before
    the dynamic ``/{wf_id}`` route so it isn't swallowed by it."""
    return list_graph_workflow_examples()
