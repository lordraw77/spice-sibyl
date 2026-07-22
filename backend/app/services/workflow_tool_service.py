"""
Phase 41 — workflows published as tools (roadmap fase 9.1).

An **active** workflow that declares an input contract (fase 6.4) and has
``expose_as_tool`` set becomes a first-class callable tool:

* exposed to ``llm.agent`` nodes and the product chat as an OpenAI-format
  function def, namespaced ``workflow__<id>`` so it never collides with
  built-in / MCP (``mcp__``) / custom (``custom__``) tools;
* invocation runs the workflow inline as a normal run (queue, stats and audit
  apply) and returns its sink output as the tool result;
* an **anti-recursion guard** (contextvars depth counter) caps the depth of a
  tool → workflow → tool chain at ``GRAPH_WORKFLOW_TOOL_MAX_DEPTH`` so a
  workflow that (transitively) calls itself cannot recurse forever.

The same tool metadata drives the MCP server (fase 9.2), so ``tool_def`` /
``get_tool_definitions`` are shared.
"""

import contextvars
import json
import logging

import aiosqlite

from app.core.config import settings
from app.db import graph_workflow_repository as repo
from app.schemas.graph_workflows import ExposedWorkflowToolOut, GraphWorkflowOut

logger = logging.getLogger(__name__)

_TOOL_PREFIX = "workflow"
_SEP = "__"

_MAX_RESULT_CHARS = 12000

# Depth of the current tool → workflow → tool chain, propagated across the
# inline (awaited) run so a self-referential workflow can't recurse forever.
_call_depth: contextvars.ContextVar[int] = contextvars.ContextVar("workflow_tool_depth", default=0)


def namespaced(workflow_id: str) -> str:
    return f"{_TOOL_PREFIX}{_SEP}{workflow_id}"


def is_workflow_tool(name: str) -> bool:
    return name.startswith(f"{_TOOL_PREFIX}{_SEP}")


def raw_id(name: str) -> str:
    return name[len(_TOOL_PREFIX) + len(_SEP):]


def _parameters(wf: GraphWorkflowOut) -> dict:
    """The JSON-Schema parameters of the tool — the workflow's input contract,
    normalised to an object schema (LLM/MCP tool params must be an object)."""
    schema = wf.input_schema if isinstance(wf.input_schema, dict) else {}
    if schema.get("type") != "object":
        schema = {"type": "object", "properties": {}, **schema, "type": "object"}
    schema.setdefault("properties", {})
    return schema


def _description(wf: GraphWorkflowOut) -> str:
    return (wf.description or f"Runs the '{wf.name}' workflow.").strip()


def tool_def(wf: GraphWorkflowOut) -> dict:
    """OpenAI-format function def for one exposed workflow."""
    return {
        "type": "function",
        "function": {
            "name": namespaced(wf.id),
            "description": _description(wf),
            "parameters": _parameters(wf),
        },
    }


def descriptor(wf: GraphWorkflowOut) -> ExposedWorkflowToolOut:
    return ExposedWorkflowToolOut(
        tool_name=namespaced(wf.id),
        workflow_id=wf.id,
        workflow_name=wf.name,
        description=_description(wf),
        parameters=_parameters(wf),
    )


async def get_tool_definitions(db: aiosqlite.Connection, profile_id: str) -> list[dict]:
    """OpenAI-format defs for every workflow the profile publishes as a tool."""
    workflows = await repo.list_exposed_tool_workflows(db, profile_id)
    return [tool_def(wf) for wf in workflows]


async def list_descriptors(db: aiosqlite.Connection, profile_id: str) -> list[ExposedWorkflowToolOut]:
    workflows = await repo.list_exposed_tool_workflows(db, profile_id)
    return [descriptor(wf) for wf in workflows]


async def call_tool(name: str, arguments: dict, profile_id: str) -> str:
    """Route a namespaced ``workflow__<id>`` call: run the workflow inline and
    return its sink output as a JSON string. Enforces the tool-chain depth cap
    and that the workflow is actually exposed to callers."""
    from app.services import workflow_graph_service as engine

    wf_id = raw_id(name)
    depth = _call_depth.get()
    max_depth = max(1, int(settings.graph_workflow_tool_max_depth))
    if depth >= max_depth:
        return (
            f"Workflow tool '{wf_id}' refused: tool→workflow chain depth "
            f"({depth}) would exceed the limit ({max_depth}); possible recursion."
        )

    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    try:
        wf = await repo.get_workflow(db, wf_id)
        if wf is None or wf.profile_id != profile_id:
            return f"Unknown workflow tool '{wf_id}'."
        if not (wf.expose_as_tool and wf.active and wf.input_schema):
            return f"Workflow '{wf.name}' is not currently exposed as a tool."
        token = _call_depth.set(depth + 1)
        try:
            result = await engine.run_workflow_sync(
                db, wf_id, profile_id,
                trigger_type="tool", trigger_payload=arguments or {}, depth=depth + 1,
            )
        finally:
            _call_depth.reset(token)
    except ValueError as exc:  # contract violation / not found
        return f"Workflow tool '{wf_id}' failed: {exc}"
    except Exception as exc:  # noqa: BLE001 — surface as a tool error, never crash the caller
        logger.exception("Workflow tool '%s' raised", wf_id)
        return f"Workflow tool '{wf_id}' failed: {exc}"
    finally:
        await db.close()

    if result.get("status") != "completed":
        return f"Workflow tool '{wf_id}' did not complete ({result.get('status')}): {result.get('error') or 'unknown error'}"
    output = result.get("output")
    text = output if isinstance(output, str) else json.dumps(output, default=str, ensure_ascii=False)
    if len(text) > _MAX_RESULT_CHARS:
        text = text[:_MAX_RESULT_CHARS] + "\n[Truncated]"
    return text
