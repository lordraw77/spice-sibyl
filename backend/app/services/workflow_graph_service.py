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
Per-node ``retry``/``backoff`` and the ``onError`` policy (stop | continue |
branch — the latter routes ``{error, input}`` through a dedicated ``error``
handle; ``continueOnFail`` is the legacy alias of ``continue``) bound failures.
``http.request`` calls external APIs directly and ``subworkflow`` runs another
workflow inline as an observable child run (nesting capped). Runs are
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
import re
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
_MAX_SUBWORKFLOW_DEPTH = 5
_HTTP_MAX_TIMEOUT = 120.0
_WAIT_MAX_SECONDS = 3600.0
_SCHEDULE_POLL_SECONDS = 20
_ENV_WHITELIST_PREFIX = "WF_"  # only WF_*-prefixed env vars are exposed as $env

# Live SSE subscribers, keyed by run id.
_subscribers: dict[str, list[asyncio.Queue]] = {}
# Fire-and-forget run tasks so they aren't garbage-collected.
_run_tasks: set[asyncio.Task] = set()
# run id → its task, so a run can be cancelled from the registry.
_tasks_by_run: dict[str, asyncio.Task] = {}
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
    _tasks_by_run[run_id] = task

    def _cleanup(t: asyncio.Task) -> None:
        _run_tasks.discard(t)
        _tasks_by_run.pop(run_id, None)

    task.add_done_callback(_cleanup)


async def cancel_run(db: aiosqlite.Connection, run_id: str) -> bool:
    """Stop a run from the registry. Cancels the live task when this process
    owns it; otherwise (stale row after a restart) marks the run cancelled
    directly. Returns False when the run is already in a terminal state."""
    task = _tasks_by_run.get(run_id)
    if task is not None and not task.done():
        task.cancel()
        return True
    status = await repo.get_run_status(db, run_id)
    if status in ("pending", "running"):
        await repo.set_run_status(db, run_id, "cancelled")
        _publish(run_id, {"kind": "run", "status": "cancelled"})
        _publish(run_id, {"kind": "done"})
        return True
    return False


# ── scheduler ───────────────────────────────────────────────────────────────

async def _execute(
    run_id: str,
    profile_id: str,
    graph: WorkflowGraph,
    trigger_type: str,
    trigger_payload: dict,
    depth: int = 0,
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
        node_semaphore = asyncio.Semaphore(max(1, settings.graph_workflow_max_concurrent_nodes))

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
            "_depth": depth,  # subworkflow nesting level (recursion guard)
        }

        def is_root(nid: str) -> bool:
            return not incoming[nid]

        def is_entry(nid: str) -> bool:
            # Only *trigger* roots start on their own; a non-trigger node that
            # was dropped on the canvas but never wired up must not fire at run
            # start — it gets recorded as skipped instead (n8n semantics).
            return is_root(nid) and nodes[nid].type in _TRIGGER_TYPES

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
                and (is_entry(nid) or has_live_input(nid))
            ]
            # Nodes whose inputs are all resolved but none are live → skip them
            # (including unwired non-trigger roots).
            newly_skipped = [
                nid
                for nid in nodes
                if nid not in done
                and nid not in skipped
                and not is_entry(nid)
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
                async with node_semaphore:
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
        if run_error:
            await _maybe_alert_recurring_failures(db, run_id, profile_id)

    except asyncio.CancelledError:
        await repo.set_run_status(db, run_id, "cancelled")
        _publish(run_id, {"kind": "run", "status": "cancelled"})
        _publish(run_id, {"kind": "done"})
        raise
    except Exception as exc:  # noqa: BLE001 — a run failure must be recorded, not raised
        logger.exception("Graph run %s crashed", run_id)
        await repo.set_run_status(db, run_id, "failed", error=str(exc))
        _publish(run_id, {"kind": "run", "status": "failed", "error": str(exc)})
        _publish(run_id, {"kind": "done"})
    finally:
        await db.close()


async def _maybe_alert_recurring_failures(db: aiosqlite.Connection, run_id: str, profile_id: str) -> None:
    """Raise an in-app alert the moment a workflow's consecutive-failure streak
    first reaches the configured threshold (no dedicated counter column — derived
    from the recent run history so it doesn't re-notify on every failure after)."""
    try:
        run = await repo.get_run(db, run_id)
        if run is None:
            return
        threshold = settings.graph_workflow_run_failure_alert_threshold
        if threshold <= 0:
            return
        recent = await repo.list_runs(db, run.workflow_id, limit=threshold + 1)
        # Fire exactly once: the newest `threshold` runs are all failures, and
        # either there's no older run or it wasn't a failure (streak just started).
        streak_just_reached = (
            len(recent) >= threshold
            and all(r.status == "failed" for r in recent[:threshold])
            and (len(recent) == threshold or recent[threshold].status != "failed")
        )
        if streak_just_reached:
            from app.services import notification_service

            wf = await repo.get_workflow(db, run.workflow_id)
            name = wf.name if wf else run.workflow_id
            await notification_service.notify_web(
                db, profile_id, "workflow",
                "Workflow in errore ripetuto",
                f"Il workflow '{name}' ha fallito {threshold} esecuzioni consecutive.",
            )
    except Exception:  # noqa: BLE001 — alerting must never break run completion
        logger.exception("failed to check recurring-failure alert for run %s", run_id)


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

    if node.continueOnFail or node.onError == "continue":
        await repo.finish_node_run(db, nr_id, "ok", output={"error": last_err}, error=last_err)
        _publish(run_id, {"kind": "node", "node_id": node.id, "status": "ok", "output": {"error": last_err}})
        return "ok", {"error": last_err}, ["main"], None

    if node.onError == "branch":
        # Route the failure through the dedicated 'error' handle: the node run is
        # recorded as an error, but the run continues down the error branch with
        # {error, input} as payload (edges on 'main' go dead → their targets skip).
        output = {"error": last_err, "input": node_input}
        await repo.finish_node_run(db, nr_id, "error", output=output, error=last_err)
        _publish(run_id, {"kind": "node", "node_id": node.id, "status": "error", "error": last_err})
        return "ok", output, ["error"], None

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

    if ntype == "wait":
        return await _exec_wait(params), ["main"]

    if ntype == "aggregate":
        return _exec_aggregate(params, node_input), ["main"]

    if ntype == "batch":
        return _exec_batch(params, node_input), ["main"]

    if ntype == "http.request":
        return await _exec_http_request(params), ["main"]

    if ntype == "subworkflow":
        return await _exec_subworkflow(db, profile_id, params, node_input, ctx), ["main"]

    if ntype == "notify.telegram":
        return await _exec_notify_telegram(db, profile_id, params), ["main"]

    if ntype == "notify.email":
        return await _exec_notify_email(params), ["main"]

    if ntype == "notify.webhook":
        return await _exec_notify_webhook(params, node_input), ["main"]

    if ntype == "notify.inapp":
        return await _exec_notify_inapp(db, profile_id, params), ["main"]

    if ntype == "llm.completion":
        return await _exec_llm_completion(db, profile_id, params), ["main"]

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


async def _exec_wait(params: dict) -> dict:
    """Suspend the node for a fixed duration or until a point in time.

    ``seconds`` sleeps that many seconds; ``until`` accepts a unix timestamp
    (number) or an ISO-8601 string and sleeps the remaining delta. Either way
    the effective delay is capped at ``_WAIT_MAX_SECONDS`` so a mistyped date
    (or a distant one) can't hang a run indefinitely.
    """
    until = params.get("until")
    if until not in (None, ""):
        from datetime import datetime, timezone

        if isinstance(until, (int, float)):
            target = float(until)
        else:
            target = datetime.fromisoformat(str(until).replace("Z", "+00:00")).timestamp()
        delay = target - time.time()
    else:
        delay = float(params.get("seconds") or 0)

    delay = max(0.0, min(delay, _WAIT_MAX_SECONDS))
    await asyncio.sleep(delay)
    return {"waited": delay}


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


async def _exec_http_request(params: dict) -> dict:
    """Generic HTTP call. Non-2xx raises by default so retry/onError apply;
    set ``allow_errors`` to get the response back regardless of status."""
    import httpx

    url = str(params.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("http.request: 'url' must be an http(s) URL")
    method = str(params.get("method") or "GET").upper()

    headers = params.get("headers") if isinstance(params.get("headers"), dict) else None
    query = params.get("query") if isinstance(params.get("query"), dict) else None
    timeout = min(float(params.get("timeout") or 30.0), _HTTP_MAX_TIMEOUT)

    body = params.get("body")
    body_kwargs: dict = {}
    if isinstance(body, (dict, list)):
        body_kwargs["json"] = body
    elif body is not None and str(body) != "":
        body_kwargs["content"] = str(body)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.request(method, url, params=query, headers=headers, **body_kwargs)

    text = resp.text
    if len(text) > _TOOL_RESULT_MAX_CHARS:
        text = text[:_TOOL_RESULT_MAX_CHARS] + "\n[Truncated]"
    parsed = None
    if "json" in (resp.headers.get("content-type") or ""):
        try:
            parsed = resp.json()
        except ValueError:
            parsed = None

    if not resp.is_success and not _as_bool(params.get("allow_errors")):
        raise RuntimeError(f"http.request: {method} {url} → HTTP {resp.status_code}: {text[:300]}")

    return {
        "status": resp.status_code,
        "ok": resp.is_success,
        "headers": dict(resp.headers),
        "json": parsed,
        "text": text,
    }


async def _exec_subworkflow(
    db: aiosqlite.Connection, profile_id: str, params: dict, node_input, ctx: dict
) -> dict:
    """Run another workflow of the same profile inline as a child run and return
    its sink outputs. ``payload`` (or the node input) becomes the child's $trigger."""
    wf_id = str(params.get("workflow_id") or "").strip()
    if not wf_id:
        raise ValueError("subworkflow: 'workflow_id' is required")

    depth = int(ctx.get("_depth") or 0)
    if depth + 1 > _MAX_SUBWORKFLOW_DEPTH:
        raise RuntimeError(f"subworkflow: max nesting depth ({_MAX_SUBWORKFLOW_DEPTH}) exceeded")

    wf = await repo.get_workflow(db, wf_id)
    if wf is None or wf.profile_id != profile_id:
        raise ValueError("subworkflow: workflow not found")

    payload = params.get("payload")
    if not isinstance(payload, dict):
        payload = {"input": node_input if payload is None else payload}

    graph_json = json.dumps(wf.graph.model_dump())
    child_run_id = await repo.create_run(db, wf_id, profile_id, "subworkflow", graph_json)
    # Inline (awaited) child execution: the parent node completes when the child
    # run does, and the child is observable like any other run (rows + SSE).
    await _execute(child_run_id, profile_id, wf.graph, "subworkflow", payload, depth=depth + 1)

    child = await repo.get_run(db, child_run_id)
    if child is None or child.status != "completed":
        raise RuntimeError(f"subworkflow: child run failed: {(child.error if child else None) or 'unknown error'}")

    node_outputs = ((await repo.get_run_context(db, child_run_id)) or {}).get("node", {})
    sources = {e.source for e in wf.graph.edges}
    sinks = [n.id for n in wf.graph.nodes if n.id not in sources]
    if len(sinks) == 1:
        output = node_outputs.get(sinks[0], {}).get("output")
    else:
        output = {s: node_outputs.get(s, {}).get("output") for s in sinks}
    return {"run_id": child_run_id, "workflow_id": wf_id, "status": child.status, "output": output}


# ── notification nodes ──────────────────────────────────────────────────────

def _notify_text(params: dict) -> str:
    text = params.get("text") or params.get("message") or params.get("body") or ""
    if not isinstance(text, str):
        text = json.dumps(text, default=str, ensure_ascii=False)
    return text


_TELEGRAM_PARSE_MODES = frozenset({"", "Markdown", "MarkdownV2", "HTML"})
# CommonMark-style **bold** (what LLM nodes typically produce) isn't valid Telegram
# Markdown/MarkdownV2 — both dialects use a single asterisk for bold — so normalise
# it rather than silently rendering the literal '**' in the chat.
_DOUBLE_STAR_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


async def _exec_notify_telegram(db: aiosqlite.Connection, profile_id: str, params: dict) -> dict:
    """Send to the profile's linked Telegram chat via the Phase 23.c bridge.
    The bridge is best-effort (missing link / muted chat / stopped bot = no-op),
    so report whether a link exists rather than pretending delivery."""
    from app.db import telegram_link_repository
    from app.services import notification_service

    text = _notify_text(params)
    if not text:
        raise ValueError("notify.telegram: 'text' is required")
    parse_mode = str(params.get("parse_mode") or "").strip()
    if parse_mode not in _TELEGRAM_PARSE_MODES:
        raise ValueError(f"notify.telegram: invalid parse_mode {parse_mode!r} (Markdown|MarkdownV2|HTML)")
    if parse_mode in ("Markdown", "MarkdownV2"):
        text = _DOUBLE_STAR_BOLD_RE.sub(r"*\1*", text)
    link = await telegram_link_repository.get_by_profile_id(db, profile_id)
    if link is None:
        raise RuntimeError("notify.telegram: no Telegram chat linked to this profile")
    await notification_service.notify_telegram(db, profile_id, "workflow", text, parse_mode=parse_mode or None)
    return {"queued": True, "channel": "telegram", "parse_mode": parse_mode or None}


async def _exec_notify_email(params: dict) -> dict:
    from app.services import email_service

    to = params.get("to") or ""
    subject = str(params.get("subject") or "SpiceSibyl workflow notification")
    body = _notify_text(params)
    return await email_service.send_email(to, subject, body)


async def _exec_notify_webhook(params: dict, node_input) -> dict:
    """POST a JSON payload to an external webhook (Slack/Discord/ntfy/…)."""
    payload = params.get("payload")
    if payload is None:
        payload = node_input
    out = await _exec_http_request({
        "method": str(params.get("method") or "POST"),
        "url": params.get("url"),
        "headers": params.get("headers"),
        "body": payload,
        "timeout": params.get("timeout"),
    })
    return {"sent": True, "status": out["status"], "response": out["json"] if out["json"] is not None else out["text"]}


async def _exec_notify_inapp(db: aiosqlite.Connection, profile_id: str, params: dict) -> dict:
    """Push a notification to the web UI bell (persisted + live SSE)."""
    from app.services import notification_service

    title = str(params.get("title") or "Workflow")
    body = _notify_text(params)
    await notification_service.notify_web(db, profile_id, "workflow", title, body)
    return {"queued": True, "channel": "inapp", "title": title}


async def _cached_complete(request) -> tuple[dict, str]:
    """Complete a chat request through the Phase 19/26 response cache (same dance as
    ChatService.complete — see chat_service.py:275-307), so identical workflow LLM node
    runs skip the provider like chat does. Returns (response_dict, "hit"|"semantic"|"miss").
    cache_service.cache_key() already returns None for tool-bearing/multimodal requests,
    so tool-using llm.agent steps are naturally excluded from caching."""
    from app.services import cache_service
    from app.services.chat_service import ChatService
    from app.services.provider_factory import ProviderFactory

    cache_key = cache_service.cache_key(request)
    cached = cache_service.get(cache_key)
    if cached is not None:
        return ChatService._cached_completion(request, cached, semantic=False), "hit"

    query_embedding: list[float] | None = None
    embed_model: str | None = None
    bucket: str | None = None
    if cache_key is not None and settings.semantic_cache_enabled:
        sem, query_embedding, embed_model, bucket = await cache_service.semantic_get(request)
        if sem is not None:
            return ChatService._cached_completion(request, sem, semantic=True), "semantic"

    provider = ProviderFactory.get_provider(request.model)
    response = await provider.complete(request)
    if hasattr(response, "model_dump"):
        response = response.model_dump()
    try:
        choices = response.get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        cache_service.put(
            cache_key, content, {"usage": response.get("usage") or {}},
            embedding=query_embedding, embed_model=embed_model, bucket=bucket,
        )
    except (AttributeError, TypeError, KeyError, IndexError):
        pass  # non-dict/odd provider response — skip caching
    return response, "miss"


async def _candidate_models(db: aiosqlite.Connection, model: str, failover_chain: str | None) -> list[str]:
    """[model] plus any further models from a named Settings → Models failover chain
    (Phase 31.c), in order, deduplicated. [model] alone when no chain is configured."""
    candidates = [model]
    chain_name = str(failover_chain or "").strip()
    if chain_name:
        from app.db import settings_repository
        from app.schemas.features import MODEL_FAILOVER_CHAINS_OWNER_KEY, failover_chain_models

        blob = await settings_repository.get(db, MODEL_FAILOVER_CHAINS_OWNER_KEY)
        for candidate in failover_chain_models(blob, chain_name):
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


async def _exec_llm_completion(db: aiosqlite.Connection, profile_id: str, params: dict) -> dict:
    from app.schemas.chat import ChatCompletionRequest, ChatMessage

    model = params.get("model") or settings.default_model
    prompt = params.get("prompt") or params.get("input") or ""
    system = params.get("system")
    messages = []
    if system:
        messages.append(ChatMessage(role="system", content=str(system)))
    messages.append(ChatMessage(role="user", content=str(prompt)))

    candidates = await _candidate_models(db, model, params.get("failover_chain"))
    tried: list[str] = []
    last_exc: Exception | None = None
    for candidate in candidates:
        tried.append(candidate)
        request = ChatCompletionRequest(
            model=candidate, messages=messages, stream=False, profile_id=profile_id
        )
        try:
            response, cache_status = await _cached_complete(request)
        except Exception as exc:  # noqa: BLE001 — fall through to the next chain candidate
            last_exc = exc
            continue
        choices = response.get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
        out = {"content": content, "model": candidate, "_usage": _extract_usage(response), "_cache": cache_status}
        if len(candidates) > 1:
            out["_failover"] = {"tried": tried, "used": candidate}
        return out
    raise last_exc


def _extract_usage(response: dict) -> dict | None:
    """Token counts from a provider response, when it reported any (Phase 30.d
    observability — no per-model cost table exists in the repo yet, so cost is
    intentionally omitted rather than guessed)."""
    usage = response.get("usage") or {}
    if not usage:
        return None
    return {
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
        "tokens_total": usage.get("total_tokens"),
    }


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
    usage_total = {"tokens_in": 0, "tokens_out": 0, "tokens_total": 0}

    def _accumulate(response: dict) -> None:
        step_usage = _extract_usage(response)
        if not step_usage:
            return
        for k in usage_total:
            usage_total[k] += step_usage.get(k) or 0

    candidates = await _candidate_models(db, model, params.get("failover_chain"))
    model_idx = 0  # sticky: once a candidate succeeds, later steps start from it
    tried: list[str] = []

    def _failover_meta(used: str) -> dict | None:
        return {"tried": tried, "used": used} if len(candidates) > 1 else None

    for _ in range(max_steps):
        response = cache_status = last_exc = None
        for idx in range(model_idx, len(candidates)):
            candidate = candidates[idx]
            if candidate not in tried:
                tried.append(candidate)
            request = ChatCompletionRequest(
                model=candidate, messages=messages, tools=tools or None,
                stream=False, profile_id=profile_id,
            )
            try:
                response, cache_status = await _cached_complete(request)
            except Exception as exc:  # noqa: BLE001 — fall through to the next chain candidate
                last_exc = exc
                continue
            model_idx = idx
            model = candidate
            break
        if response is None:
            raise last_exc
        _accumulate(response)
        choices = response.get("choices") or []
        if not choices:
            break
        choice = choices[0]
        msg = choice.get("message") or {}
        tool_calls_raw = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        if choice.get("finish_reason") != "tool_calls" or not tool_calls_raw:
            out = {"content": content, "model": model, "_usage": usage_total, "_cache": cache_status}
            failover = _failover_meta(model)
            if failover:
                out["_failover"] = failover
            return out
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

    out = {"content": "Step limit reached without a final answer.", "model": model, "_usage": usage_total}
    failover = _failover_meta(model)
    if failover:
        out["_failover"] = failover
    return out


async def _note_trigger_success(db: aiosqlite.Connection, tr_id: str) -> None:
    await repo.record_trigger_success(db, tr_id)


async def _note_trigger_failure(
    db: aiosqlite.Connection, tr_id: str, workflow_id: str, profile_id: str, error: str
) -> None:
    """Bump the trigger's consecutive-failure streak; auto-disable and alert past
    the configured threshold so a broken schedule/event trigger doesn't fail
    silently forever (previously only logged, see Phase 30.b)."""
    fail_count = await repo.record_trigger_failure(db, tr_id, error)
    if fail_count >= settings.graph_workflow_trigger_max_failures:
        await repo.set_trigger_enabled(db, tr_id, False)
        try:
            from app.services import notification_service

            wf = await repo.get_workflow(db, workflow_id)
            name = wf.name if wf else workflow_id
            await notification_service.notify_web(
                db, profile_id, "workflow",
                "Trigger disabilitato",
                f"Il trigger del workflow '{name}' è stato disabilitato dopo {fail_count} "
                f"fallimenti consecutivi. Ultimo errore: {error}",
            )
        except Exception:  # noqa: BLE001 — alerting must never break the poll loop
            logger.exception("failed to raise trigger-disabled alert for %s", tr_id)


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
                        await _note_trigger_success(db, row["id"])
                        if nxt is None:
                            await repo.set_trigger_enabled(db, row["id"], False)
                    except Exception as exc:
                        logger.exception("schedule trigger firing failed id=%s", row.get("id"))
                        await _note_trigger_failure(
                            db, row["id"], row["workflow_id"], row["wf_profile_id"], str(exc)
                        )
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
                    await _note_trigger_success(db, row["id"])
                except Exception as exc:
                    logger.exception("event trigger firing failed id=%s", row.get("id"))
                    await _note_trigger_failure(
                        db, row["id"], row["workflow_id"], row["wf_profile_id"], str(exc)
                    )
        finally:
            await db.close()
    except Exception:
        logger.exception("dispatch_event failed for %s", event_type)
