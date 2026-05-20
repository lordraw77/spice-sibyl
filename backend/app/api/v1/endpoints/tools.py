from fastapi import APIRouter
from app.tools.registry import TOOL_DEFINITIONS

router = APIRouter()


@router.get("")
async def list_tools():
    return TOOL_DEFINITIONS
