"""
Phase 18 — persistent multi-step workflow (agent run) service.

Runs a durable server-side agent loop as a background asyncio task: the model
is given a goal plus the full tool registry (built-ins, MCP, custom tools) and
iterates until it produces a final answer or hits ``max_steps`` — well beyond
the 5-iteration chat loop.

Durability model:

* every assistant turn / tool call / tool result is appended to
  ``agent_run_steps`` (the inspectable trace);
* the serialized message history is checkpointed on ``agent_runs.messages``
  after **every** iteration, so pause/resume — and recovery after a process
  restart — replays nothing and loses at most the in-flight iteration;
* the loop re-reads ``status`` from the DB between iterations: the pause and
  cancel endpoints just flip the status and the loop obeys at the next
  boundary. Runs left in ``running`` with no live task (a restart happened
  mid-run) are reconciled to ``paused`` on the next list/get.
"""

import asyncio
import json
import logging

import aiosqlite

from app.core.config import settings
from app.db import workflow_repository
from app.schemas.chat import ChatCompletionRequest, ChatMessage, ToolCall, ToolCallFunction
from app.schemas.workflows import AgentRunOut
from app.services.provider_factory import ProviderFactory
from app.tools.registry import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an autonomous agent executing a multi-step task. Work towards the goal "
    "using the available tools; call them whenever they help. Think step by step, keep "
    "intermediate notes concise, and when the goal is achieved reply with the final "
    "answer (no further tool calls)."
)

_TOOL_RESULT_MAX_CHARS = 12000

# Live background tasks, keyed by run id.
_tasks: dict[str, asyncio.Task] = {}


def is_live(run_id: str) -> bool:
    task = _tasks.get(run_id)
    return task is not None and not task.done()


async def _connect() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def _tool_definitions(db: aiosqlite.Connection, profile_id: str) -> list[dict]:
    """Full tool set for a run: built-ins + discovered MCP tools + custom tools."""
    from app.services import custom_tool_service, mcp_service

    tools = list(TOOL_DEFINITIONS)
    try:
        await mcp_service.refresh(db)
        tools.extend(mcp_service.get_tool_definitions())
    except Exception:  # noqa: BLE001 — a broken MCP server must not block the run
        logger.exception("Workflow: MCP discovery failed; continuing without MCP tools")
    try:
        tools.extend(await custom_tool_service.get_tool_definitions(db, profile_id))
    except Exception:  # noqa: BLE001
        logger.exception("Workflow: custom tool listing failed; continuing without them")
    return tools


def _serialize(messages: list[ChatMessage]) -> str:
    return json.dumps([m.model_dump(exclude_none=True) for m in messages])


def _deserialize(raw: str) -> list[ChatMessage]:
    return [ChatMessage.model_validate(m) for m in json.loads(raw)]


def start(run_id: str, system_prompt: str | None = None) -> None:
    """Spawn (or resume) the background loop for a run."""
    if is_live(run_id):
        return
    task = asyncio.get_running_loop().create_task(_run_loop(run_id, system_prompt))
    _tasks[run_id] = task
    task.add_done_callback(lambda _t: _tasks.pop(run_id, None))


async def pause(db: aiosqlite.Connection, run_id: str) -> bool:
    """Request a pause: the loop stops at the next iteration boundary."""
    status = await workflow_repository.get_status(db, run_id)
    if status not in ("pending", "running"):
        return False
    await workflow_repository.set_status(db, run_id, "paused")
    return True


async def resume(db: aiosqlite.Connection, run_id: str) -> bool:
    status = await workflow_repository.get_status(db, run_id)
    if status != "paused":
        return False
    await workflow_repository.set_status(db, run_id, "running")
    start(run_id)
    return True


async def cancel(db: aiosqlite.Connection, run_id: str) -> bool:
    status = await workflow_repository.get_status(db, run_id)
    if status not in ("pending", "running", "paused"):
        return False
    await workflow_repository.set_status(db, run_id, "cancelled")
    task = _tasks.get(run_id)
    if task and not task.done():
        task.cancel()
    return True


async def reconcile(db: aiosqlite.Connection, run: AgentRunOut) -> AgentRunOut:
    """A run marked running with no live task was interrupted by a restart —
    surface it as paused (its last checkpoint makes it resumable)."""
    if run.status == "running" and not is_live(run.id):
        await workflow_repository.set_status(db, run.id, "paused")
        run.status = "paused"
    return run


async def _run_loop(run_id: str, system_prompt: str | None = None) -> None:
    db = await _connect()
    try:
        run = await workflow_repository.get_run(db, run_id)
        if run is None:
            return
        await workflow_repository.set_status(db, run_id, "running")

        # Resume from the checkpoint when present, else seed the conversation.
        raw = await workflow_repository.get_messages_json(db, run_id)
        if raw:
            messages = _deserialize(raw)
        else:
            prompt = _SYSTEM_PROMPT if not system_prompt else f"{_SYSTEM_PROMPT}\n\n{system_prompt}"
            messages = [
                ChatMessage(role="system", content=prompt),
                ChatMessage(role="user", content=run.goal),
            ]
            await workflow_repository.checkpoint(db, run_id, _serialize(messages), 0)

        tools = await _tool_definitions(db, run.profile_id)
        provider = ProviderFactory.get_provider(run.model)
        step = run.current_step

        while step < run.max_steps:
            # Obey pause/cancel requested via the API between iterations.
            status = await workflow_repository.get_status(db, run_id)
            if status != "running":
                logger.info("Workflow %s stopping: status=%s", run_id, status)
                return

            request = ChatCompletionRequest(
                model=run.model,
                messages=messages,
                tools=tools or None,
                stream=False,
                profile_id=run.profile_id,
            )
            response = await provider.complete(request)
            if hasattr(response, "model_dump"):
                response = response.model_dump()

            choices = response.get("choices") or []
            if not choices:
                raise RuntimeError("Provider returned no choices")
            choice = choices[0]
            msg = choice.get("message") or {}
            tool_calls_raw = msg.get("tool_calls") or []
            content = msg.get("content") or ""

            if choice.get("finish_reason") != "tool_calls" or not tool_calls_raw:
                # Final answer.
                messages.append(ChatMessage(role="assistant", content=content))
                await workflow_repository.add_step(db, run_id, step, "final", content)
                await workflow_repository.checkpoint(db, run_id, _serialize(messages), step + 1)
                await workflow_repository.set_status(db, run_id, "completed", result=content)
                logger.info("Workflow %s completed in %d step(s)", run_id, step + 1)
                return

            if content.strip():
                await workflow_repository.add_step(db, run_id, step, "assistant", content)

            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    type=tc.get("type", "function"),
                    function=ToolCallFunction(
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    ),
                )
                for tc in tool_calls_raw
            ]
            messages.append(ChatMessage(
                role="assistant", content=msg.get("content"), tool_calls=tool_calls
            ))

            for tc in tool_calls:
                func_name = tc.function.name
                try:
                    func_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}
                await workflow_repository.add_step(
                    db, run_id, step, "tool_call",
                    json.dumps(func_args, ensure_ascii=False), name=func_name,
                )
                try:
                    result = await execute_tool(func_name, func_args, profile_id=run.profile_id)
                except (RuntimeError, ValueError, OSError) as exc:
                    result = f"Error: {exc}"
                if len(result) > _TOOL_RESULT_MAX_CHARS:
                    result = result[:_TOOL_RESULT_MAX_CHARS] + "\n[Truncated]"
                await workflow_repository.add_step(
                    db, run_id, step, "tool_result", result, name=func_name
                )
                messages.append(ChatMessage(role="tool", tool_call_id=tc.id, content=result))

            step += 1
            await workflow_repository.checkpoint(db, run_id, _serialize(messages), step)

        # Loop exhausted without a final answer.
        error = f"Step limit reached ({run.max_steps}) without a final answer."
        await workflow_repository.add_step(db, run_id, step, "error", error)
        await workflow_repository.set_status(db, run_id, "failed", error=error)
        logger.warning("Workflow %s failed: %s", run_id, error)

    except asyncio.CancelledError:
        # Cancel endpoint already set the status; just stop quietly.
        logger.info("Workflow %s task cancelled", run_id)
    except Exception as exc:  # noqa: BLE001 — a run failure must be recorded, not raised
        logger.exception("Workflow %s crashed", run_id)
        try:
            run = await workflow_repository.get_run(db, run_id)
            await workflow_repository.add_step(
                db, run_id, run.current_step if run else 0, "error", str(exc)
            )
            await workflow_repository.set_status(db, run_id, "failed", error=str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("Workflow %s: could not record failure", run_id)
    finally:
        await db.close()
