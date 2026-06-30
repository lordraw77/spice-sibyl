import logging

import aiosqlite
from fastapi import APIRouter, Depends

from app.db.database import get_db
from app.services import mcp_service
from app.tools.registry import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def list_tools(db: aiosqlite.Connection = Depends(get_db)):
    """Built-in tools plus every tool discovered from enabled MCP servers.

    Refreshing here (re-probing enabled servers) keeps the routing cache the chat
    tool-loop relies on warm: the frontend fetches this before enabling tools.
    Discovery failures are swallowed so a broken MCP server never hides the
    built-in tools.
    """
    tools = list(TOOL_DEFINITIONS)
    try:
        await mcp_service.refresh(db)
        tools.extend(mcp_service.get_tool_definitions())
    except Exception:  # noqa: BLE001 — MCP is optional; never break the tool list
        logger.exception("MCP tool discovery failed; returning built-in tools only")
    return tools
