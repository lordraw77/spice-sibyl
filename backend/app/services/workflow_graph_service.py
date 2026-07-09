"""
Phase 29 — visual node-graph workflow engine.

A deterministic **topological scheduler** over a DAG of typed nodes. For each
run it seeds the trigger payload, then repeatedly executes every node whose
inputs are resolved — independent ready nodes run **in parallel** via
``asyncio.gather`` — until the graph is drained. Each node:

1. takes its primary input (``$json``) from the first *live* incoming edge;
2. resolves its declared ``params`` through :mod:`expression_resolver`;
3. executes via the executor for its ``type``;
4. persists a ``workflow_node_run`` and checkpoints the run context;
5. activates one or more output handles, marking the matching outgoing edges
   live and propagating the output to successors.

Branch nodes (``if``/``switch``) activate a single handle; edges on the other
handles go *dead* and their exclusive targets are recorded as ``skipped``.
Per-node ``retry``/``backoff`` and ``continueOnFail`` bound failures. Runs are
durable (every node run + the run context are persisted) and stream live over
SSE (``subscribe``/``publish``), mirroring the Phase 23.c notification stream.

Reuse: node executors wrap the **existing tool registry** (``tool.<name>`` — RSS,
read_url, weather, kb_search, python_exec, MCP, custom tools; zero new code per
node), the ``python_exec`` sandbox (``code`` node), the provider factory
(``llm.completion``) and the Phase 18 agent loop (``llm.agent``). The schedule
poll loop reuses ``reminder_parsing`` for cron/RRULE/NL → ``next_run_at``.
"""

import asyncio
import json
import logging
import os
import time

import aiosqlite

from app.core.config import settings
from app.db import graph_workflow_repository as repo
from app.schemas.graph_workflows import GraphNode, WorkflowGraph

logger = logging.getLogger(__name__)

_TRIGGER_TYPES = frozenset({"manual", "schedule", "webhook", "event"})
_LOOP_TYPES = frozenset({"for", "repeat"})
_TOOL_RESULT_MAX_CHARS = 12000
_MAX_LOOP_ITERATIONS = 1000
_SCHEDULE_POLL_SECONDS = 20
_ENV_WHITELIST_PREFIX = "WF_"  # only WF_*-prefixed env vars are exposed as $env

# Live SSE subscribers, keyed by run id.
_subscribers: dict[str, list[asyncio.Queue]] = {}
# Fire-and-forget run tasks so they aren't garbage-collected.
_run_tasks: set[asyncio.Task] = set()
_poll_task: asyncio.Task | None = None


# ── SSE bus ─────────────────────────────────────────────────────────────────

def subscribe(run_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(run_id, []).append(queue)
    return queue


def unsubscribe(run_id: str, queue: asyncio.Queue) -> None:
    queues = _subscribers.get(run_id)
    if not queues:
        return
    if queue in queues:
        queues.remove(queue)
    if not queues:
        _subscribers.pop(run_id, None)


def _publish(run_id: str, event: dict) -> None:
    for queue in _subscribers.get(run_id, []):
        queue.put_nowait(event)


# ── connection helper ───────────────────────────────────────────────────────

async def _connect() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    return db


def _env_context() -> dict:
    return {
        k[len(_ENV_WHITELIST_PREFIX):]: v
        for k, v in os.environ.items()
        if k.startswith(_ENV_WHITELIST_PREFIX)
    }


# ── public entry point ──────────────────────────────────────────────────────

async def run_workflow(
    db: aiosqlite.Connection,
    workflow_id: str,
    profile_id: str,
    *,
    trigger_type: str = "manual",
    trigger_payload: dict | None = None,
    graph: WorkflowGraph | None = None,
) -> str:
    """Create a run row and start executing the graph in the background.

    Returns the run id immediately; progress is observable via ``get_run`` /
    the SSE stream.
    """
    if graph is None:
        wf = await repo.get_workflow(db, workflow_id)
        if wf is None:
            raise ValueError("workflow not found")
        graph = wf.graph

    graph_json = json.dumps(graph.model_dump())
    run_id = await repo.create_run(db, workflow_id, profile_id, trigger_type, graph_json)
    _spawn(run_id, profile_id, graph, trigger_type, trigger_payload or {})
    return run_id


def _spawn(
    run_id: str, profile_id: str, graph: WorkflowGraph, trigger_type: str, trigger_payload: dict
) -> None:
    """Detach the graph execution as a background task. Isolated so tests can
    drive ``_execute`` deterministically (the TestClient's per-request loop
    cancels fire-and-forget tasks — see ``tests/test_phase29.py``)."""
    task = asyncio.get_running_loop().create_task(
        _execute(run_id, profile_id, graph, trigger_type, trigger_payload)
    )
    _run_tasks.add(task)
    task.add_done_callback(_run_tasks.discard)


# ── scheduler ───────────────────────────────────────────────────────────────

async def _execute(
    run_id: str,
    profile_id: str,
    graph: WorkflowGraph,
    trigger_type: str,
    trigger_payload: dict,
) -> None:
    db = await _connect()
    try:
        await repo.set_run_status(db, run_id, "running")
        _publish(run_id, {"kind": "run", "status": "running"})

        # If the graph uses MCP tool nodes, make sure the MCP routing cache is warm
        # so execute_tool can dispatch mcp__* names on this fresh connection.
        if any(n.type.startswith("tool.mcp__") for n in graph.nodes):
            try:
                from app.services import mcp_service
                await mcp_service.refresh(db)
            except Exception:  # noqa: BLE001 — a broken MCP server must not block the run
                logger.exception("Graph run %s: MCP refresh failed; mcp nodes may error", run_id)

        nodes: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
        edges = graph.edges

        # Incoming/outgoing adjacency.
        incoming: dict[str, list] = {nid: [] for nid in nodes}
        outgoing: dict[str, list] = {nid: [] for nid in nodes}
        for e in edges:
            if e.target in incoming:
                incoming[e.target].append(e)
            if e.source in outgoing:
                outgoing[e.source].append(e)

        # Node execution state.
        done: set[str] = set()
        skipped: set[str] = set()
        live_edges: set[str] = set()   # edge ids whose source routed through them
        dead_edges: set[str] = set()   # edge ids the source explicitly bypassed

        ctx: dict = {
            "node": {},
            "trigger": trigger_payload,
            "env": _env_context(),
            "now": int(time.time()),
        }

        def is_root(nid: str) -> bool:
            return not incoming[nid]

        def edges_resolved(nid: str) -> bool:
            # Every incoming edge's source has reached a terminal state.
            return all(e.source in done or e.source in skipped for e in incoming[nid])

        def has_live_input(nid: str) -> bool:
            return any(e.id in live_edges for e in incoming[nid])

        def primary_input(nid: str):
            # First live incoming edge, preferring the 'main' target handle.
            live_in = [e for e in incoming[nid] if e.id in live_edges]
            live_in.sort(key=lambda e: 0 if e.targetHandle == "main" else 1)
            if not live_in:
                return trigger_payload if is_root(nid) else None
            src_out = ctx["node"].get(live_in[0].source, {}).get("output")
            return src_out

        def all_live_inputs(nid: str) -> list:
            return [
                ctx["node"].get(e.source, {}).get("output")
                for e in incoming[nid]
                if e.id in live_edges
            ]

        def loop_body(loop_id: str) -> tuple[set[str], list[str]]:
            """Body of a for/repeat node: nodes forward-reachable from its 'loop'
            output minus those reachable from its 'done' output (the continuation)."""
            loop_targets = [e.target for e in outgoing[loop_id] if e.sourceHandle == "loop"]
            done_targets = [e.target for e in outgoing[loop_id] if e.sourceHandle == "done"]

            def reach(starts: list[str]) -> set[str]:
                seen: set[str] = set()
                stack = list(starts)
                while stack:
                    x = stack.pop()
                    if x in seen or x == loop_id or x not in nodes:
                        continue
                    seen.add(x)
                    stack.extend(e.target for e in outgoing.get(x, []))
                return seen

            cont = reach(done_targets)
            body = reach(loop_targets) - cont - {loop_id}
            entry = [t for t in loop_targets if t in body]
            return body, entry

        run_error: str | None = None

        # Wave loop: run every runnable node in parallel until none remain.
        while True:
            runnable = [
                nid
                for nid in nodes
                if nid not in done
                and nid not in skipped
                and edges_resolved(nid)
                and (is_root(nid) or has_live_input(nid))
            ]
            # Nodes whose inputs are all resolved but none are live → skip them.
            newly_skipped = [
                nid
                for nid in nodes
                if nid not in done
                and nid not in skipped
                and not is_root(nid)
                and edges_resolved(nid)
                and not has_live_input(nid)
            ]
            for nid in newly_skipped:
                skipped.add(nid)
                await repo.record_skipped_node(db, run_id, nid, nodes[nid].type)
                _publish(run_id, {"kind": "node", "node_id": nid, "status": "skipped"})
                for e in outgoing[nid]:
                    dead_edges.add(e.id)

            if not runnable:
                if newly_skipped:
                    continue  # skipping may unblock/close more nodes
                break

            async def _wrap(nid: str):
                node = nodes[nid]
                if node.type in _LOOP_TYPES:
                    body_ids, entry_ids = loop_body(nid)
                    outcome = await _run_loop_node(
                        db, run_id, profile_id, node, nodes, incoming, outgoing,
                        ctx, primary_input(nid), body_ids, entry_ids,
                    )
                    return nid, outcome
                node_input = all_live_inputs(nid) if node.type == "merge" else primary_input(nid)
                return nid, await _run_node(db, run_id, profile_id, node, node_input, ctx)

            results = await asyncio.gather(*(_wrap(nid) for nid in runnable), return_exceptions=True)

            for res in results:
                if isinstance(res, Exception):
                    run_error = str(res)
                    logger.exception("Graph run %s: node crashed", run_id, exc_info=res)
                    continue
                nid, outcome = res
                status, output, handles, err = outcome
                done.add(nid)
                if status == "ok":
                    ctx["node"][nid] = {"output": output}
                    active = set(handles)
                    for e in outgoing[nid]:
                        if e.sourceHandle in active:
                            live_edges.add(e.id)
                        else:
                            dead_edges.add(e.id)
                    # A loop node owns its body: the body already ran per iteration,
                    # so mark those nodes done and kill their edges in the main graph
                    # (continuation flows only through the loop's 'done' handle).
                    if nodes[nid].type in _LOOP_TYPES:
                        body_ids, _ = loop_body(nid)
                        for b in body_ids:
                            done.add(b)
                            for e in outgoing[b]:
                                dead_edges.add(e.id)
                else:  # error, not continued
                    run_error = err or f"node {nid} failed"
                    for e in outgoing[nid]:
                        dead_edges.add(e.id)

            await repo.set_run_status(db, run_id, "running", context=_ctx_snapshot(ctx))

            if run_error:
                break

        final_status = "failed" if run_error else "completed"
        await repo.set_run_status(db, run_id, final_status, context=_ctx_snapshot(ctx), error=run_error)
        _publish(run_id, {"kind": "run", "status": final_status, "error": run_error})
        _publish(run_id, {"kind": "done"})
        logger.info("Graph run %s finished: %s", run_id, final_status)

    except asyncio.CancelledError:
        await repo.set_run_status(db, run_id, "cancelled")
        _publish(run_id, {"kind": "run", "status": "cancelled"})
        raise
    except Exception as exc:  # noqa: BLE001 — a run failure must be recorded, not raised
        logger.exception("Graph run %s crashed", run_id)
        await repo.set_run_status(db, run_id, "failed", error=str(exc))
        _publish(run_id, {"kind": "run", "status": "failed", "error": str(exc)})
        _publish(run_id, {"kind": "done"})
    finally:
        await db.close()


def _ctx_snapshot(ctx: dict) -> dict:
    return {"node": ctx["node"], "trigger": ctx.get("trigger")}


async def _run_node(
    db: aiosqlite.Connection,
    run_id: str,
    profile_id: str,
    node: GraphNode,
    node_input,
    ctx: dict,
) -> tuple[str, object, list[str], str | None]:
    """Execute one node with retry/backoff. Returns (status, output, handles, error)."""
    from app.services import expression_resolver

    nr_id = await repo.start_node_run(db, run_id, node.id, node.type, node_input)
    _publish(run_id, {"kind": "node", "node_id": node.id, "status": "running"})

    local_ctx = {**ctx, "json": node_input}
    attempts = node.retry + 1
    last_err: str | None = None

    for attempt in range(attempts):
        try:
            params = await expression_resolver.resolve_params(node.params, local_ctx)
            output, handles = await _dispatch(db, profile_id, node, node_input, params, local_ctx)
            await repo.finish_node_run(db, nr_id, "ok", output=output)
            _publish(run_id, {"kind": "node", "node_id": node.id, "status": "ok", "output": _preview(output)})
            return "ok", output, handles, None
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            logger.warning("Graph node %s attempt %d/%d failed: %s", node.id, attempt + 1, attempts, exc)
            if attempt + 1 < attempts and node.backoff:
                await asyncio.sleep(node.backoff)

    if node.continueOnFail:
        await repo.finish_node_run(db, nr_id, "ok", output={"error": last_err}, error=last_err)
        _publish(run_id, {"kind": "node", "node_id": node.id, "status": "ok", "output": {"error": last_err}})
        return "ok", {"error": last_err}, ["main"], None

    await repo.finish_node_run(db, nr_id, "error", error=last_err)
    _publish(run_id, {"kind": "node", "node_id": node.id, "status": "error", "error": last_err})
    return "error", None, [], last_err


def _preview(output) -> object:
    """A size-bounded copy of a node output for the live SSE event."""
    try:
        text = json.dumps(output, default=str)
    except (TypeError, ValueError):
        return str(output)[:2000]
    return output if len(text) <= 2000 else text[:2000] + "…[truncated]"


# ── loop nodes (for / repeat) ────────────────────────────────────────────────

async def _run_loop_node(
    db: aiosqlite.Connection,
    run_id: str,
    profile_id: str,
    node: GraphNode,
    nodes: dict,
    incoming: dict,
    outgoing: dict,
    ctx: dict,
    node_input,
    body_ids: set,
    entry_ids: list,
) -> tuple[str, object, list[str], str | None]:
    """Run a ``for``/``repeat`` node: execute the body subgraph once per iteration
    (``$item``/``$index`` in scope), collect the results, then continue on ``done``.
    Returns the same (status, output, handles, error) tuple as ``_run_node``."""
    from app.services import expression_resolver

    nr_id = await repo.start_node_run(db, run_id, node.id, node.type, node_input)
    _publish(run_id, {"kind": "node", "node_id": node.id, "status": "running"})
    try:
        local_ctx = {**ctx, "json": node_input}
        params = await expression_resolver.resolve_params(node.params, local_ctx)

        if node.type == "for":
            items = params.get("items")
            if items is None:
                items = node_input
            if isinstance(items, tuple):
                items = list(items)
            if not isinstance(items, list):
                items = [] if items is None else [items]
            iterations = list(enumerate(items))[:_MAX_LOOP_ITERATIONS]
        else:  # repeat
            times = int(params.get("times") or 0)
            times = max(0, min(times, _MAX_LOOP_ITERATIONS))
            iterations = [(i, i) for i in range(times)]

        results = []
        for idx, item in iterations:
            iter_ctx = {**ctx, "item": item, "index": idx}
            out = await _run_body_once(
                db, run_id, profile_id, node, nodes, incoming, outgoing,
                body_ids, entry_ids, iter_ctx, item,
            )
            results.append(out)

        output = {"items": results, "count": len(results)}
        await repo.finish_node_run(db, nr_id, "ok", output=output)
        _publish(run_id, {"kind": "node", "node_id": node.id, "status": "ok", "output": _preview(output)})
        return "ok", output, ["done"], None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Graph loop node %s failed", node.id)
        await repo.finish_node_run(db, nr_id, "error", error=str(exc))
        _publish(run_id, {"kind": "node", "node_id": node.id, "status": "error", "error": str(exc)})
        if node.continueOnFail:
            return "ok", {"items": [], "count": 0, "error": str(exc)}, ["done"], None
        return "error", None, [], str(exc)


async def _run_body_once(
    db: aiosqlite.Connection,
    run_id: str,
    profile_id: str,
    loop_node: GraphNode,
    nodes: dict,
    incoming: dict,
    outgoing: dict,
    body_ids: set,
    entry_ids: list,
    ctx: dict,
    item,
) -> object:
    """One pass over the loop body: a small sequential topological run restricted
    to ``body_ids``. Entry nodes receive ``item`` as input; the pass returns the
    output of the body's sink node(s)."""
    done: set[str] = set()
    outputs: dict[str, object] = {}

    def internal_in(nid: str) -> list:
        return [e for e in incoming[nid] if e.source in body_ids]

    remaining = set(body_ids)
    guard = 0
    while remaining:
        guard += 1
        if guard > len(body_ids) + 2:
            break
        batch = [nid for nid in remaining if all(e.source in done for e in internal_in(nid))]
        if not batch:
            break
        for nid in batch:
            node = nodes[nid]
            ins = internal_in(nid)
            if not ins:
                node_input = item
            elif node.type == "merge":
                node_input = [outputs.get(e.source) for e in ins]
            else:
                node_input = outputs.get(ins[0].source)
            status, output, _handles, err = await _run_node(
                db, run_id, profile_id, node, node_input, ctx
            )
            done.add(nid)
            remaining.discard(nid)
            if status == "ok":
                outputs[nid] = output
            else:
                raise RuntimeError(err or f"loop body node {nid} failed")

    # Body result: the output of sink nodes (no outgoing edge inside the body).
    sinks = [nid for nid in body_ids if not any(e.target in body_ids for e in outgoing[nid])]
    if len(sinks) == 1:
        return outputs.get(sinks[0])
    return {s: outputs.get(s) for s in sinks}


# ── node executors ──────────────────────────────────────────────────────────

async def _dispatch(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, node_input, params: dict, ctx: dict
) -> tuple[object, list[str]]:
    """Return (output, active_output_handles) for a node."""
    ntype = node.type

    if ntype in _TRIGGER_TYPES:
        return node_input if node_input is not None else params, ["main"]

    if ntype.startswith("tool."):
        return await _exec_tool(profile_id, ntype[len("tool."):], params), ["main"]

    if ntype == "set":
        fields = params.get("fields")
        return (fields if isinstance(fields, dict) else params), ["main"]

    if ntype == "if":
        cond = _as_bool(params.get("condition"))
        return {"value": cond, "input": node_input}, ["true" if cond else "false"]

    if ntype == "switch":
        return _exec_switch(node, params, node_input)

    if ntype == "merge":
        items = node_input if isinstance(node_input, list) else [node_input]
        return {"items": items}, ["main"]

    if ntype == "filter":
        return _exec_filter(params, node_input), ["main"]

    if ntype == "code":
        return await _exec_code(params, node_input, ctx), ["main"]

    if ntype == "llm.completion":
        return await _exec_llm_completion(profile_id, params), ["main"]

    if ntype == "llm.agent":
        return await _exec_llm_agent(db, profile_id, params), ["main"]

    raise ValueError(f"unknown node type: {ntype}")


async def _exec_tool(profile_id: str, tool_name: str, params: dict) -> dict:
    from app.tools.registry import execute_tool

    result = await execute_tool(tool_name, params, profile_id=profile_id)
    if isinstance(result, str) and len(result) > _TOOL_RESULT_MAX_CHARS:
        result = result[:_TOOL_RESULT_MAX_CHARS] + "\n[Truncated]"
    # Tools return text; try to surface JSON structurally when they produced it.
    parsed = None
    if isinstance(result, str):
        stripped = result.strip()
        if stripped[:1] in ("{", "["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
    return {"result": result, "json": parsed}


def _exec_switch(node: GraphNode, params: dict, node_input) -> tuple[object, list[str]]:
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
        filtered = [it for it, flag in zip(items, keep) if _as_bool(flag)]
    else:
        filtered = list(items)
    return {"items": filtered, "count": len(filtered)}


async def _exec_code(params: dict, node_input, ctx: dict) -> dict:
    from app.tools.code_interpreter import python_exec

    code = params.get("code") or ""
    payload = json.dumps({"input": node_input, "node": ctx.get("node", {})}, default=str)
    wrapper = (
        "import json\n"
        f"_ctx = json.loads({payload!r})\n"
        "input = _ctx['input']\n"
        "node = _ctx['node']\n"
        + code
    )
    out = await python_exec(wrapper)
    return {"stdout": out}


async def _exec_llm_completion(profile_id: str, params: dict) -> dict:
    from app.schemas.chat import ChatCompletionRequest, ChatMessage
    from app.services.provider_factory import ProviderFactory

    model = params.get("model") or settings.default_model
    prompt = params.get("prompt") or params.get("input") or ""
    system = params.get("system")
    messages = []
    if system:
        messages.append(ChatMessage(role="system", content=str(system)))
    messages.append(ChatMessage(role="user", content=str(prompt)))

    provider = ProviderFactory.get_provider(model)
    request = ChatCompletionRequest(
        model=model, messages=messages, stream=False, profile_id=profile_id
    )
    response = await provider.complete(request)
    if hasattr(response, "model_dump"):
        response = response.model_dump()
    choices = response.get("choices") or []
    content = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
    return {"content": content, "model": model}


async def _full_tool_definitions(db: aiosqlite.Connection, profile_id: str) -> list[dict]:
    """Built-ins + discovered MCP tools + the profile's custom tools — the same
    set the Phase 18 agent loop uses, so ``llm.agent`` nodes can call them."""
    from app.services import custom_tool_service, mcp_service
    from app.tools.registry import TOOL_DEFINITIONS

    tools = list(TOOL_DEFINITIONS)
    try:
        await mcp_service.refresh(db)
        tools.extend(mcp_service.get_tool_definitions())
    except Exception:  # noqa: BLE001 — a broken MCP server must not block the run
        logger.exception("graph llm.agent: MCP discovery failed; continuing without MCP tools")
    try:
        tools.extend(await custom_tool_service.get_tool_definitions(db, profile_id))
    except Exception:  # noqa: BLE001
        logger.exception("graph llm.agent: custom tool listing failed; continuing without them")
    return tools


async def _exec_llm_agent(db: aiosqlite.Connection, profile_id: str, params: dict) -> dict:
    """Bridge node: run the Phase 18 durable agent loop to completion inline, over
    the full tool set (built-in + MCP + custom)."""
    from app.schemas.chat import ChatCompletionRequest, ChatMessage, ToolCall, ToolCallFunction
    from app.services.provider_factory import ProviderFactory
    from app.tools.registry import execute_tool

    model = params.get("model") or settings.default_model
    goal = str(params.get("goal") or params.get("prompt") or "")
    max_steps = int(params.get("max_steps") or 8)
    system = params.get("system_prompt") or (
        "You are an autonomous agent. Work towards the goal using the available "
        "tools; when done, reply with the final answer and no further tool calls."
    )
    tools = await _full_tool_definitions(db, profile_id)
    messages = [
        ChatMessage(role="system", content=str(system)),
        ChatMessage(role="user", content=goal),
    ]
    provider = ProviderFactory.get_provider(model)

    for _ in range(max_steps):
        request = ChatCompletionRequest(
            model=model, messages=messages, tools=tools or None,
            stream=False, profile_id=profile_id,
        )
        response = await provider.complete(request)
        if hasattr(response, "model_dump"):
            response = response.model_dump()
        choices = response.get("choices") or []
        if not choices:
            break
        choice = choices[0]
        msg = choice.get("message") or {}
        tool_calls_raw = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        if choice.get("finish_reason") != "tool_calls" or not tool_calls_raw:
            return {"content": content, "model": model}
        tool_calls = [
            ToolCall(
                id=tc["id"], type=tc.get("type", "function"),
                function=ToolCallFunction(name=tc["function"]["name"], arguments=tc["function"]["arguments"]),
            )
            for tc in tool_calls_raw
        ]
        messages.append(ChatMessage(role="assistant", content=msg.get("content"), tool_calls=tool_calls))
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            try:
                result = await execute_tool(tc.function.name, args, profile_id=profile_id)
            except (RuntimeError, ValueError, OSError) as exc:
                result = f"Error: {exc}"
            messages.append(ChatMessage(role="tool", tool_call_id=tc.id, content=result))

    return {"content": "Step limit reached without a final answer.", "model": model}


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


# ── schedule poll loop (29.b) ───────────────────────────────────────────────

async def _poll_loop() -> None:
    from app.services import reminder_parsing
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.timezone) if getattr(settings, "timezone", None) else ZoneInfo("UTC")
    while True:
        try:
            db = await _connect()
            try:
                due = await repo.list_due_schedule_triggers(db, int(time.time()))
                for row in due:
                    try:
                        cfg = json.loads(row["config_json"])
                        await run_workflow(
                            db,
                            row["workflow_id"],
                            row["wf_profile_id"],
                            trigger_type="schedule",
                            trigger_payload={"schedule": cfg},
                        )
                        # Recompute next_run_at from the recurrence spec.
                        recurrence = cfg.get("recurrence", "once")
                        nxt = reminder_parsing.compute_next_fire(recurrence, int(time.time()), tz)
                        await repo.set_trigger_next_run(db, row["id"], nxt)
                        if nxt is None:
                            await repo.set_trigger_enabled(db, row["id"], False)
                    except Exception:
                        logger.exception("schedule trigger firing failed id=%s", row.get("id"))
            finally:
                await db.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("workflow schedule poll iteration failed")
        await asyncio.sleep(_SCHEDULE_POLL_SECONDS)


def start_scheduler() -> None:
    global _poll_task
    if _poll_task is not None and not _poll_task.done():
        return
    _poll_task = asyncio.get_running_loop().create_task(_poll_loop())
    logger.info("workflow_graph_service: schedule poll loop started (interval=%ss)", _SCHEDULE_POLL_SECONDS)


async def stop_scheduler() -> None:
    global _poll_task
    if _poll_task is None:
        return
    _poll_task.cancel()
    try:
        await _poll_task
    except asyncio.CancelledError:
        pass
    _poll_task = None


async def dispatch_event(event_type: str, payload: dict) -> None:
    """Fire every active workflow whose ``event`` trigger matches ``event_type``.

    Best-effort — called from the notification/ingest paths. A single failure
    must never disrupt the caller.
    """
    try:
        db = await _connect()
        try:
            triggers = await repo.list_event_triggers(db, event_type)
            for row in triggers:
                try:
                    await run_workflow(
                        db, row["workflow_id"], row["wf_profile_id"],
                        trigger_type="event",
                        trigger_payload={"event": event_type, **(payload or {})},
                    )
                except Exception:
                    logger.exception("event trigger firing failed id=%s", row.get("id"))
        finally:
            await db.close()
    except Exception:
        logger.exception("dispatch_event failed for %s", event_type)
