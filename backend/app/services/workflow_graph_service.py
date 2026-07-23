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
import math
import os
import random
import re
import time
import uuid
from types import SimpleNamespace

import aiosqlite

from app.core.config import settings
from app.db import graph_workflow_repository as repo
from app.db import pool
from app.workflow import registry
from app.workflow.registry import DispatchCtx
from app.schemas.graph_workflows import GraphEdge, GraphNode, WorkflowApprovalOut, WorkflowGraph

logger = logging.getLogger(__name__)

_TRIGGER_TYPES = frozenset({
    "manual", "schedule", "webhook", "event", "error",
    # Phase 38 (roadmap fase 6): success = another workflow completed;
    # file.watch / email.inbound = poll-based external-world triggers.
    "success", "file.watch", "email.inbound",
    # Phase 41 (roadmap fase 9.3): chat = one run per conversation message.
    "chat",
    # Phase 46 (roadmap fase 14.4): queue.consume = one run per message
    # consumed off a QueueDriver topic (poll-based, like file.watch).
    "queue.consume",
    # Phase 47 (roadmap fase 15.4): rss.read = one run per new feed entry
    # (poll-based, deduped by guid).
    "rss.read",
    # Phase 52 (roadmap fase 20.1): telegram = one run per inbound bot message /
    # command, dispatched from the live bot (not poll-based).
    "telegram",
})
_LOOP_TYPES = frozenset({"for", "repeat", "while"})
_TOOL_RESULT_MAX_CHARS = 12000
_MAX_LOOP_ITERATIONS = 1000
_MAX_SUBWORKFLOW_DEPTH = 5
_HTTP_MAX_TIMEOUT = 120.0
_WAIT_MAX_SECONDS = 3600.0
_RETRY_MAX_BACKOFF_SECONDS = 60.0  # cap per pause, even with exponential growth
_SCHEDULE_POLL_SECONDS = 20
# Phase 35 (roadmap fase 4) — new node bounds.
_APPROVAL_POLL_SECONDS = 2.0        # how often a waiting human.approval re-checks its request
_DB_QUERY_MAX_ROWS = 1000           # rows returned by db.query, hard cap
_FILE_MAX_BYTES = 10 * 1024 * 1024  # file.read/file.write size guard
_ENV_WHITELIST_PREFIX = "WF_"  # only WF_*-prefixed env vars are exposed as $env
_NO_OVERRIDE = object()  # sentinel: "no node-input override supplied" (fase 8.3 step debug)

# Phase 46 (roadmap fase 14.3) — this process's lease-owner id, minted once per
# process lifetime. Stamped on a run's `lease_owner` column while this process
# executes it; a lease past its `lease_expires_at` is free for any instance
# (including this one, on restart) to reclaim. See repo.acquire_lease.
_INSTANCE_ID = str(uuid.uuid4())
# Phase 46 (roadmap fase 14.1) — node types the remote-runner dispatcher will
# ever hand off: pure functions of (params, input) needing no `db`/profile_id
# (no vault, tool registry or workspace-storage access), so a runner process
# can execute them with nothing but the resolved params it was sent.
_REMOTE_CAPABLE_TYPES = frozenset({
    "http.request", "code", "db.query", "set", "if", "switch", "merge",
    "filter", "aggregate", "batch", "wait", "queue.publish",
})


class BudgetExceededError(ValueError):
    """Fase 12.1 — a hard token/run budget cap (workflow or profile-wide) is
    exceeded for the current period. A ValueError subclass so every existing
    ``except ValueError`` call site (API endpoints, the schedule/event trigger
    firing loops) keeps working unchanged."""

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

async def _connect() -> pool.PooledConnection:
    # Borrow from the shared pool; the many `await db.close()` call sites now
    # release the connection back instead of tearing it down (see app/db/pool.py).
    return await pool.checkout()


def _env_context() -> dict:
    return {
        k[len(_ENV_WHITELIST_PREFIX):]: v
        for k, v in os.environ.items()
        if k.startswith(_ENV_WHITELIST_PREFIX)
    }


async def _secrets_context(db: aiosqlite.Connection, profile_id: str) -> dict:
    """Decrypt the profile's workflow secrets for the duration of a run —
    exposed to expressions as ``$secrets.<name>``. Never persisted:
    ``_ctx_snapshot`` drops this key before the context is checkpointed."""
    from app.services import vault_service

    encrypted = await repo.get_encrypted_secrets(db, profile_id)
    out: dict[str, str] = {}
    for name, ciphertext in encrypted.items():
        plain = vault_service.decrypt(ciphertext, settings.vault_secret_key)
        if plain is not None:
            out[name] = plain
    return out


def _environment_config(wf, environment: str | None) -> dict:
    """The fase 7.2 environment block ({vars, secrets, version}) of a workflow,
    or {} when no environment applies / it isn't defined."""
    if not environment or wf is None:
        return {}
    cfg = (wf.environments or {}).get(environment)
    return cfg if isinstance(cfg, dict) else {}


def _apply_secret_bindings(secrets: dict, env_cfg: dict) -> dict:
    """Fase 7.2 — remap $secrets aliases through the environment's bindings
    ({alias: real_secret_name}): the alias resolves to the bound secret's value;
    unbound names keep resolving directly."""
    bindings = env_cfg.get("secrets")
    if not isinstance(bindings, dict) or not bindings:
        return secrets
    out = dict(secrets)
    for alias, real_name in bindings.items():
        if isinstance(real_name, str) and real_name in secrets:
            out[str(alias)] = secrets[real_name]
    return out


async def _workflow_variables(db: aiosqlite.Connection, run_id: str) -> dict:
    """The owning workflow's ``$vars`` for a run (empty when unavailable). A run
    executing in a fase 7.2 environment sees the environment's ``vars`` overlaid
    on the workflow's own."""
    run = await repo.get_run(db, run_id)
    if run is None:
        return {}
    wf = await repo.get_workflow(db, run.workflow_id)
    base = dict((wf.variables if wf else None) or {})
    env_vars = _environment_config(wf, run.environment if run else None).get("vars")
    if isinstance(env_vars, dict):
        base.update(env_vars)
    return base


def _month_period(now: int | None = None) -> tuple[str, int]:
    """Fase 12.1 — the current UTC calendar-month period as ("YYYY-MM", epoch
    of its first second). Budgets reset "for free" because usage is always
    queried with ``created_at >= period_start`` rather than via a counter."""
    import calendar

    t = time.gmtime(now if now is not None else time.time())
    label = f"{t.tm_year:04d}-{t.tm_mon:02d}"
    period_start = calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, 0))
    return label, period_start


async def _warn_budget(
    db: aiosqlite.Connection, profile_id: str, scope: str, label: str, mark_id: str,
    tokens_used: int, token_budget: int | None, runs_used: int, run_budget: int | None,
    period: str, already_warned: str | None, mark_warned,
) -> None:
    """Fase 12.1 — a one-time in-app notification per period when usage first
    crosses ``GRAPH_WORKFLOW_BUDGET_WARN_PCT`` of either cap. Never raises: a
    notification hiccup must not block the run it is warning about."""
    if already_warned == period:
        return
    pct = settings.graph_workflow_budget_warn_pct
    hit = (
        (token_budget and token_budget > 0 and tokens_used >= token_budget * pct) or
        (run_budget and run_budget > 0 and runs_used >= run_budget * pct)
    )
    if not hit:
        return
    try:
        from app.services import notification_service

        await notification_service.notify_web(
            db, profile_id, "workflow",
            "Budget in esaurimento",
            f"{scope} '{label}' ha superato il {int(pct * 100)}% del budget mensile "
            f"({tokens_used} token / {token_budget or '∞'}, {runs_used} run / {run_budget or '∞'}).",
        )
        await mark_warned(db, mark_id, period)
    except Exception:  # noqa: BLE001 — a warning must never break the run it warns about
        logger.exception("failed to raise budget-warning alert for %s %s", scope, label)


async def _check_budget(db: aiosqlite.Connection, wf, profile_id: str) -> None:
    """Fase 12.1 — raises :class:`BudgetExceededError` when the workflow's own
    monthly LLM-token/run cap, or the profile-wide ("workspace") cap, is fully
    used up for the current period. Also fires the fase-12.1 soft warning once
    usage first crosses the configured threshold. A no-op when neither the
    workflow nor the profile define any cap."""
    if wf is None:
        return
    period, period_start = _month_period()

    if wf.token_budget_month is not None or wf.run_budget_month is not None:
        usage = await repo.workflow_usage_for_period(db, wf.id, period_start)
        if (
            (wf.token_budget_month is not None and usage["tokens_total"] >= wf.token_budget_month) or
            (wf.run_budget_month is not None and usage["runs"] >= wf.run_budget_month)
        ):
            raise BudgetExceededError(
                f"workflow '{wf.name}' has reached its monthly budget "
                f"({usage['tokens_total']} tokens / {usage['runs']} runs used this period)"
            )
        await _warn_budget(
            db, profile_id, "Workflow", wf.name, wf.id,
            usage["tokens_total"], wf.token_budget_month, usage["runs"], wf.run_budget_month,
            period, wf.budget_warned_period, repo.set_workflow_budget_warned,
        )

    profile_budget = await repo.get_profile_budget(db, profile_id)
    if profile_budget and (profile_budget["token_budget_month"] is not None or profile_budget["run_budget_month"] is not None):
        usage = await repo.profile_usage_for_period(db, profile_id, period_start)
        if (
            (profile_budget["token_budget_month"] is not None and usage["tokens_total"] >= profile_budget["token_budget_month"]) or
            (profile_budget["run_budget_month"] is not None and usage["runs"] >= profile_budget["run_budget_month"])
        ):
            raise BudgetExceededError(
                f"profile-wide monthly budget reached "
                f"({usage['tokens_total']} tokens / {usage['runs']} runs used this period)"
            )
        await _warn_budget(
            db, profile_id, "Workspace", profile_id, profile_id,
            usage["tokens_total"], profile_budget["token_budget_month"], usage["runs"], profile_budget["run_budget_month"],
            period, profile_budget["warned_period"], repo.set_profile_budget_warned,
        )


# ── public entry point ──────────────────────────────────────────────────────

async def run_workflow(
    db: aiosqlite.Connection,
    workflow_id: str,
    profile_id: str,
    *,
    trigger_type: str = "manual",
    trigger_payload: dict | None = None,
    graph: WorkflowGraph | None = None,
    start_node_id: str | None = None,
    environment: str | None = None,
    origin_run_id: str | None = None,
    debug: bool = False,
    breakpoints: list[str] | None = None,
    priority: int = 0,
) -> str:
    """Create a run row and start executing the graph in the background.

    Returns the run id immediately; progress is observable via ``get_run`` /
    the SSE stream. When ``start_node_id`` is set the run is **partial**:
    only that node and its downstream subgraph execute, with every other
    node seeded from its latest persisted output.

    Fase 2.3 — when the workflow has ``max_concurrent_runs`` > 0 and that many
    runs are already active, the run is created in status ``queued`` (its
    trigger payload parked in the run context) and starts when a slot frees.
    Partial runs bypass the queue: they are interactive editor actions.

    Fase 7.2 — ``environment`` names a workflow environment: its ``vars`` /
    ``secrets`` bindings apply to the run, and a pinned ``version`` replaces
    the current graph (unless the caller pinned a start node). Fase 7.1 —
    ``origin_run_id`` records the run this one was retried/replayed from.
    """
    wf = await repo.get_workflow(db, workflow_id)
    environment = (environment or "").strip() or None
    if environment is not None:
        if wf is None or not isinstance((wf.environments or {}).get(environment), dict):
            raise ValueError(f"environment '{environment}' is not defined on this workflow")
        pinned = _environment_config(wf, environment).get("version")
        if pinned and start_node_id is None:
            pinned_graph = await repo.get_version_graph(db, workflow_id, int(pinned))
            if pinned_graph is None:
                raise ValueError(f"environment '{environment}' pins version {pinned}, which no longer exists")
            graph = pinned_graph
    if graph is None:
        if wf is None:
            raise ValueError("workflow not found")
        graph = wf.graph

    # Fase 12.1 — a partial/dev run (start_node_id) or a step-debug run never
    # counts against a budget; every other trigger type (manual, schedule,
    # webhook, event, success, chat, subworkflow, tool/MCP invocation) does.
    if start_node_id is None and not debug:
        await _check_budget(db, wf, profile_id)

    if wf is not None and wf.max_concurrent_runs > 0 and start_node_id is None:
        active_runs = await repo.count_active_runs(db, workflow_id)
        if active_runs >= wf.max_concurrent_runs:
            graph_json = json.dumps(graph.model_dump())
            run_id = await repo.create_run(
                db, workflow_id, profile_id, trigger_type, graph_json,
                status="queued", context={"node": {}, "trigger": trigger_payload or {}},
                environment=environment, origin_run_id=origin_run_id, priority=priority,
            )
            logger.info("Graph run %s queued (workflow %s at %d/%d active runs)",
                        run_id, workflow_id, active_runs, wf.max_concurrent_runs)
            return run_id

    seed_outputs: dict[str, object] | None = None
    if start_node_id is not None:
        if start_node_id not in {n.id for n in graph.nodes}:
            raise ValueError("start node not in graph")
        hist = await repo.latest_node_outputs(db, workflow_id)
        seed_outputs = {nid: entry["output"] for nid, entry in hist.items()}
        # Fase 3.2 — a pinned output beats history when seeding a dev partial run.
        for n in graph.nodes:
            if n.pinnedOutput is not None:
                seed_outputs[n.id] = n.pinnedOutput

    graph_json = json.dumps(graph.model_dump())

    # Fase 8.3 — a step-debug run is born ``paused`` with an empty checkpoint and
    # is NOT spawned; it advances one node at a time via ``debug_run`` (the
    # POST /runs/{id}/debug endpoint). The requested breakpoints are stored on it.
    if debug:
        valid = {n.id for n in graph.nodes}
        bps = [b for b in (breakpoints or []) if b in valid]
        run_id = await repo.create_run(
            db, workflow_id, profile_id, trigger_type, graph_json,
            status="paused", context={"node": {}, "trigger": trigger_payload or {}},
            environment=environment, origin_run_id=origin_run_id, priority=priority,
        )
        await repo.set_run_debug(db, run_id, {"breakpoints": bps, "pending_node": None})
        _publish(run_id, {"kind": "run", "status": "paused"})
        return run_id

    run_id = await repo.create_run(
        db, workflow_id, profile_id, trigger_type, graph_json,
        environment=environment, origin_run_id=origin_run_id, priority=priority,
    )
    _spawn(run_id, profile_id, graph, trigger_type, trigger_payload or {},
           start_node_id=start_node_id, seed_outputs=seed_outputs)
    return run_id


async def _resolve_dedup_key(config: dict, payload: dict) -> str | None:
    """Fase 16.2 — evaluate a trigger's ``dedupKey`` expression against the
    delivered payload (exposed as ``$trigger``). Returns the string key, or None
    when the trigger has no dedup key or it resolves to empty. A bad expression
    disables dedup for that delivery rather than dropping it."""
    expr = (config or {}).get("dedupKey")
    if not expr or not isinstance(expr, str):
        return None
    from app.services import expression_resolver

    ctx = {"trigger": payload or {}, "json": payload, "now": int(time.time())}
    try:
        value = await expression_resolver.resolve_value(expr, ctx)
    except Exception:  # noqa: BLE001 — a broken dedup key must not drop the delivery
        logger.warning("dedup key expression failed to resolve: %s", expr)
        return None
    if value in (None, ""):
        return None
    return value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)


async def run_from_trigger(
    db: aiosqlite.Connection, trigger, wf, trigger_type: str, payload: dict,
) -> tuple[str, bool]:
    """Start a run for an external trigger (webhook/event) applying idempotency
    (fase 16.2) and priority (fase 16.4) from the trigger config.

    ``trigger`` is a ``WorkflowTriggerOut``-like object exposing ``id`` and
    ``config``. Returns ``(run_id, deduped)`` — ``deduped=True`` means a prior,
    still-fresh delivery of the same key is being returned instead of a new run.
    """
    config = trigger.config or {}
    key = await _resolve_dedup_key(config, payload)
    window = config.get("dedupWindowSeconds")
    window = int(window) if isinstance(window, (int, float)) and not isinstance(window, bool) else None
    if key is not None and window is None:
        window = settings.graph_workflow_dedup_default_window_seconds
    priority = config.get("priority")
    priority = int(priority) if isinstance(priority, (int, float)) and not isinstance(priority, bool) else 0

    if key is not None and window and window > 0:
        existing = await repo.dedup_lookup(db, trigger.id, key, int(time.time()))
        if existing is not None:
            logger.info("Trigger %s dedup hit for key=%s → run %s", trigger.id, key, existing)
            return existing, True

    run_id = await run_workflow(
        db, wf.id, wf.profile_id, trigger_type=trigger_type, trigger_payload=payload,
        graph=wf.graph, environment=config.get("environment"), priority=priority,
    )
    if key is not None and window and window > 0:
        await repo.dedup_record(db, trigger.id, key, run_id, int(time.time()) + window)
    return run_id, False


async def debug_run(
    db: aiosqlite.Connection, run_id: str, command: str,
    breakpoints: list[str] | None = None, input_override: object | None = None,
    has_input: bool = False,
) -> dict:
    """Fase 8.3 — advance a ``paused`` step-debug run. ``step`` runs the next
    node then pauses again; ``continue`` runs until the next breakpoint (or the
    end); ``stop`` cancels the run. ``breakpoints`` replaces the run's breakpoint
    set; ``input_override`` (when ``has_input``) mocks the next node's primary
    input for this step. Returns {status}."""
    run = await repo.get_run(db, run_id)
    if run is None:
        raise ValueError("run not found")
    if run.status != "paused":
        raise ValueError(f"only paused runs can be debugged (run is {run.status})")

    if command == "stop":
        await repo.set_run_status(db, run_id, "cancelled")
        await repo.set_run_debug(db, run_id, None)
        _publish(run_id, {"kind": "run", "status": "cancelled"})
        _publish(run_id, {"kind": "done"})
        return {"status": "cancelled"}

    graph_json = await repo.get_run_graph(db, run_id)
    try:
        graph = WorkflowGraph.model_validate(json.loads(graph_json or ""))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"run has an invalid graph snapshot: {exc}") from None

    dbg = await repo.get_run_debug(db, run_id) or {}
    if breakpoints is not None:
        valid = {n.id for n in graph.nodes}
        dbg["breakpoints"] = [b for b in breakpoints if b in valid]
    if has_input:
        dbg["input"] = input_override
    else:
        dbg.pop("input", None)
    await repo.set_run_debug(db, run_id, dbg)

    persisted = await repo.get_run_context(db, run_id) or {}
    payload = persisted.get("trigger") or {}
    _spawn(run_id, run.profile_id, graph, run.trigger_type, payload,
           resume=True, debug_mode=command)
    return {"status": "running"}


async def cancel_stale_debug_runs() -> None:
    """Fase 8.3 — cancel step-debug runs left ``paused`` past the configured
    timeout so a forgotten debug session doesn't stay suspended forever. Called
    from the scheduler tick and at startup."""
    ttl = settings.graph_workflow_debug_max_pause
    if ttl <= 0:
        return
    db = await _connect()
    try:
        cutoff = int(time.time()) - ttl
        for run in await repo.list_stale_paused_runs(db, cutoff):
            await repo.set_run_status(db, run.id, "cancelled", error="debug session timed out")
            await repo.set_run_debug(db, run.id, None)
            _publish(run.id, {"kind": "run", "status": "cancelled", "error": "debug session timed out"})
            _publish(run.id, {"kind": "done"})
            logger.info("Graph run %s cancelled: debug session timed out", run.id)
    finally:
        await db.close()


async def purge_stale_chat_sessions() -> None:
    """Fase 9.3 — delete chat sessions idle past ``GRAPH_WORKFLOW_CHAT_SESSION_TTL``.
    Called from the scheduler tick. TTL <= 0 disables expiry."""
    ttl = settings.graph_workflow_chat_session_ttl
    if ttl <= 0:
        return
    db = await _connect()
    try:
        removed = await repo.purge_stale_chat_sessions(db, int(time.time()) - ttl)
        if removed:
            logger.info("Purged %d idle chat session(s)", removed)
    finally:
        await db.close()


async def purge_old_runs() -> None:
    """Fase 12.2 — delete terminal runs (and their cascaded node runs) past the
    workflow's own ``runs_retention_days`` or the global
    ``GRAPH_WORKFLOW_RUNS_RETENTION_DAYS`` default. Called from the scheduler
    tick; both 0 (global default) means "keep forever" unless a workflow set
    its own override, since :func:`repo.purge_old_runs` only purges a workflow
    without an override when the *global* default itself is > 0."""
    default_days = settings.graph_workflow_runs_retention_days
    db = await _connect()
    try:
        removed = await repo.purge_old_runs(db, default_days, int(time.time()))
        if removed:
            logger.info("Purged %d run(s) past retention", removed)
    finally:
        await db.close()


async def purge_expired_state_and_dedup() -> None:
    """Fase 16.1/16.2 — reclaim persistent-state keys and trigger-dedup entries
    whose TTL has passed. Reads already apply lazy expiry, so this only keeps the
    tables bounded; it never changes observable behaviour. Called from the tick."""
    db = await _connect()
    try:
        now = int(time.time())
        s = await repo.purge_expired_state(db, now)
        d = await repo.purge_expired_dedup(db, now)
        if s or d:
            logger.info("Purged %d expired state key(s) and %d dedup ent(y/ies)", s, d)
    finally:
        await db.close()


async def retry_run(db: aiosqlite.Connection, run_id: str) -> str:
    """Fase 7.1 — relaunch a **failed** run from its failed node(s): a new run
    is created over the origin's exact graph snapshot, seeded with the outputs
    already computed in the origin's checkpoint, and only the missing subgraph
    re-executes (the same mechanics as the fase 2.4 crash resume, on explicit
    request). Returns the new run id; the origin run is left untouched and the
    derived run records ``origin_run_id``."""
    origin = await repo.get_run(db, run_id)
    if origin is None:
        raise ValueError("run not found")
    if origin.status != "failed":
        raise ValueError(f"only failed runs can be retried (run is {origin.status})")
    graph_json = await repo.get_run_graph(db, run_id)
    try:
        graph = WorkflowGraph.model_validate(json.loads(graph_json or ""))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"run has an invalid graph snapshot: {exc}") from None

    persisted = await repo.get_run_context(db, run_id) or {}
    payload = persisted.get("trigger") or {}
    new_run_id = await repo.create_run(
        db, origin.workflow_id, origin.profile_id, origin.trigger_type, graph_json,
        # The checkpointed node outputs seed the new run; the fase 2.4 resume
        # path re-derives live edges from their recorded handles and executes
        # only what is missing (the failed node and everything after it).
        context={"node": persisted.get("node") or {}, "trigger": payload},
        environment=origin.environment, origin_run_id=run_id,
    )
    _spawn(new_run_id, origin.profile_id, graph, origin.trigger_type, payload, resume=True)
    return new_run_id


def _node_signature(node) -> dict:
    """The parts of a node that count as a *config* change for the version diff
    (fase 8.1): its position on the canvas is deliberately excluded — moving a
    node is not a change."""
    data = node.model_dump()
    data.pop("position", None)
    return data


async def diff_versions(
    db: aiosqlite.Connection, wf_id: str, from_version: int, to_version: int
) -> dict:
    """Fase 8.1 — structural diff between two saved graph versions. Returns node
    ids grouped as added / removed / changed / unchanged plus edge id deltas, so
    the editor can paint the ``to`` graph (added green, removed red, changed
    yellow) and show the per-node config diff in the inspector."""
    a = await repo.get_version_graph(db, wf_id, from_version)
    b = await repo.get_version_graph(db, wf_id, to_version)
    if a is None:
        raise ValueError(f"version {from_version} not found")
    if b is None:
        raise ValueError(f"version {to_version} not found")

    a_nodes = {n.id: n for n in a.nodes}
    b_nodes = {n.id: n for n in b.nodes}
    added = [nid for nid in b_nodes if nid not in a_nodes]
    removed = [nid for nid in a_nodes if nid not in b_nodes]
    changed: list[dict] = []
    unchanged: list[str] = []
    for nid in b_nodes:
        if nid not in a_nodes:
            continue
        before, after = _node_signature(a_nodes[nid]), _node_signature(b_nodes[nid])
        if before == after:
            unchanged.append(nid)
        else:
            changed.append({"id": nid, "before": before, "after": after})

    a_edges = {f"{e.source}:{e.sourceHandle}->{e.target}:{e.targetHandle}" for e in a.edges}
    b_edges = {f"{e.source}:{e.sourceHandle}->{e.target}:{e.targetHandle}" for e in b.edges}
    return {
        "from_version": from_version,
        "to_version": to_version,
        "added_nodes": added,
        "removed_nodes": removed,
        "changed_nodes": changed,
        "unchanged_nodes": unchanged,
        "added_edges": sorted(b_edges - a_edges),
        "removed_edges": sorted(a_edges - b_edges),
    }


def _spawn(
    run_id: str, profile_id: str, graph: WorkflowGraph, trigger_type: str, trigger_payload: dict,
    start_node_id: str | None = None, seed_outputs: dict | None = None, resume: bool = False,
    debug_mode: str | None = None,
) -> None:
    """Detach the graph execution as a background task. Isolated so tests can
    drive ``_execute`` deterministically (the TestClient's per-request loop
    cancels fire-and-forget tasks — see ``tests/test_phase29.py``)."""
    task = asyncio.get_running_loop().create_task(
        _execute(run_id, profile_id, graph, trigger_type, trigger_payload,
                 start_node_id=start_node_id, seed_outputs=seed_outputs, resume=resume,
                 debug_mode=debug_mode)
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
    if status in ("queued", "pending", "running", "waiting"):
        await repo.set_run_status(db, run_id, "cancelled")
        await repo.cancel_pending_approvals(db, run_id)
        _publish(run_id, {"kind": "run", "status": "cancelled"})
        _publish(run_id, {"kind": "done"})
        return True
    return False


async def preview_expression(db: aiosqlite.Connection, workflow_id: str, expression: str) -> dict:
    """Evaluate an expression read-only against the workflow's latest run data.

    The context mirrors what a node would see at run time: ``$node`` from the
    latest persisted output of every node (cross-run), ``$trigger`` from the
    most recent run's context, plus ``$env`` / ``$now``. Returns
    ``{ok: True, value}`` or ``{ok: False, error}`` — never raises.
    """
    from app.services import expression_resolver

    node_ctx: dict[str, dict] = {}
    hist = await repo.latest_node_outputs(db, workflow_id)
    for nid, entry in hist.items():
        node_ctx[nid] = {"output": entry.get("output")}
    trigger: dict = {}
    runs = await repo.list_runs(db, workflow_id, limit=1)
    if runs:
        run_ctx = await repo.get_run_context(db, runs[0].id) or {}
        trigger = run_ctx.get("trigger") or {}
        for nid, entry in (run_ctx.get("node") or {}).items():
            node_ctx.setdefault(nid, entry)
    wf = await repo.get_workflow(db, workflow_id)
    # Secrets stay usable in previews ($secrets.NAME resolves) but masked, so
    # the editor can never be used to read a stored secret back in plaintext.
    masked_secrets = {}
    if wf is not None:
        masked_secrets = {
            name: "***" for name in await repo.get_encrypted_secrets(db, wf.profile_id)
        }
        # Fase 3.2 — pinned outputs beat run history in editor previews too.
        for n in wf.graph.nodes:
            if n.pinnedOutput is not None:
                node_ctx[n.id] = {"output": n.pinnedOutput}
    ctx = {
        "node": node_ctx,
        "trigger": trigger,
        "env": _env_context(),
        "vars": (wf.variables if wf else None) or {},
        "secrets": masked_secrets,
        "now": int(time.time()),
        "item": None,
        "index": None,
    }
    try:
        value = await expression_resolver.resolve_value(expression, ctx)
        return {"ok": True, "value": _preview(value)}
    except Exception as exc:  # noqa: BLE001 — surface the error to the editor UI
        return {"ok": False, "error": str(exc)}


async def test_node(
    db: aiosqlite.Connection,
    workflow_id: str,
    profile_id: str,
    node_id: str,
    *,
    node_override: GraphNode | None = None,
    input_override: object | None = None,
) -> dict:
    """Fase 3.1 — execute ONE node in isolation and return its output inline.

    No run/node-run rows are created: this is the editor's "run this node"
    debugging action, not an execution. The context mirrors a partial run —
    ``$node`` from each node's pinned output (fase 3.2) or latest persisted
    output, ``$trigger`` from the most recent run — and the node's primary
    input comes from ``input_override``, else from the first incoming edge's
    seeded output, else from the trigger payload. Retries are intentionally
    skipped (a test should fail fast); the per-attempt timeout still applies.
    Returns ``{ok, output, handles, input, duration_ms}`` or
    ``{ok: False, error, input, duration_ms}`` — never raises on node failure.
    """
    from app.services import expression_resolver

    wf = await repo.get_workflow(db, workflow_id)
    if wf is None:
        raise ValueError("workflow not found")
    nodes = {n.id: n for n in wf.graph.nodes}
    node = node_override if node_override is not None else nodes.get(node_id)
    if node is None or node.id != node_id or (node_override is not None and node_id not in nodes):
        raise ValueError("node not in graph")
    if node.type in _LOOP_TYPES:
        return {"ok": False, "error": "for/repeat/while nodes cannot be tested in isolation — use 'run from this node' instead", "input": None, "duration_ms": 0}

    node_ctx: dict[str, dict] = {}
    for nid, entry in (await repo.latest_node_outputs(db, workflow_id)).items():
        node_ctx[nid] = {"output": entry.get("output")}
    for n in wf.graph.nodes:
        if n.pinnedOutput is not None:
            node_ctx[n.id] = {"output": n.pinnedOutput}
    trigger: dict = {}
    runs = await repo.list_runs(db, workflow_id, limit=1)
    if runs:
        trigger = ((await repo.get_run_context(db, runs[0].id)) or {}).get("trigger") or {}

    if input_override is not None:
        node_input = input_override
    else:
        node_input = trigger
        incoming = sorted(
            (e for e in wf.graph.edges if e.target == node_id),
            key=lambda e: 0 if e.targetHandle == "main" else 1,
        )
        for e in incoming:
            if e.source in node_ctx:
                node_input = node_ctx[e.source].get("output")
                break

    ctx = {
        "node": node_ctx,
        "trigger": trigger,
        "env": _env_context(),
        "vars": wf.variables or {},
        "secrets": await _secrets_context(db, profile_id),
        "now": int(time.time()),
        "item": None,
        "index": None,
        "_depth": 0,
        "_run_id": None,  # no run: human.approval refuses to execute in a node test
        "_workflow_id": workflow_id,  # fase 16.1 — state.* nodes read/write real state
        "json": node_input,
    }
    started = time.time()
    try:
        params = await expression_resolver.resolve_params(node.params, ctx)
        dispatch = _dispatch(db, profile_id, node, node_input, params, ctx)
        timeout_s = node.timeoutMs / 1000.0 if node.timeoutMs > 0 else None
        if timeout_s is not None:
            try:
                output, handles = await asyncio.wait_for(dispatch, timeout_s)
            except asyncio.TimeoutError:
                raise TimeoutError(f"node timed out after {node.timeoutMs} ms") from None
        else:
            output, handles = await dispatch
        return {
            "ok": True,
            "output": _jsonable(output),
            "handles": list(handles),
            "input": _preview(node_input),
            "duration_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 — surface the failure to the editor UI
        return {
            "ok": False,
            "error": str(exc),
            "input": _preview(node_input),
            "duration_ms": int((time.time() - started) * 1000),
        }


def _jsonable(value) -> object:
    """A JSON-safe copy of a node output (the test result travels as JSON and
    may be pinned verbatim by the editor, so keep it full-fidelity, not the
    size-bounded ``_preview`` used for SSE frames)."""
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)


# ── scheduler ───────────────────────────────────────────────────────────────

async def _execute(
    run_id: str,
    profile_id: str,
    graph: WorkflowGraph,
    trigger_type: str,
    trigger_payload: dict,
    depth: int = 0,
    start_node_id: str | None = None,
    seed_outputs: dict | None = None,
    resume: bool = False,
    debug_mode: str | None = None,
    dry_run: bool = False,
    use_pins: bool = False,
) -> None:
    db = await _connect()
    try:
        # Fase 14.3 — claim the run's lease before doing any work. On a single
        # process this always succeeds (nothing else could hold it); it only
        # ever blocks a *second* live instance from executing the same run
        # concurrently. A stale lease (past its expiry — e.g. the owner
        # crashed) is reclaimed automatically by repo.acquire_lease's
        # conditional UPDATE, which is how a restarted/other instance takes
        # over via the ordinary fase 2.4 resume path.
        if not await repo.acquire_lease(db, run_id, _INSTANCE_ID, settings.graph_workflow_lease_ttl_seconds):
            logger.warning("Graph run %s: lease held by another live instance, skipping", run_id)
            return
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

        # Fase 7.2 — a run in a named environment sees the environment's vars
        # overlaid on the workflow's and its $secrets aliases remapped.
        run_row = await repo.get_run(db, run_id)
        wf_row = await repo.get_workflow(db, run_row.workflow_id) if run_row else None
        env_cfg = _environment_config(wf_row, run_row.environment if run_row else None)

        ctx: dict = {
            "node": {},
            "trigger": trigger_payload,
            "env": _env_context(),
            "vars": await _workflow_variables(db, run_id),
            "secrets": _apply_secret_bindings(await _secrets_context(db, profile_id), env_cfg),
            "now": int(time.time()),
            "_depth": depth,  # subworkflow nesting level (recursion guard)
            "_run_id": run_id,  # fase 4.4 — lets human.approval bind its request to the run
            "_workflow_id": run_row.workflow_id if run_row else None,  # fase 16.1 — state.* scope
            # Fase 11.1/11.2 — external-effect nodes (http.request/db.query/
            # notification.*/llm.*) may be mocked: `_use_pins` lets a pinned
            # output (fase 3.2) replace the real call when present; `_dry_run`
            # additionally mocks those without a pin with a typed placeholder
            # and never performs the real call. See `_mock_dispatch`.
            "_use_pins": use_pins,
            "_dry_run": dry_run,
        }

        def is_root(nid: str) -> bool:
            return not incoming[nid]

        def is_entry(nid: str) -> bool:
            # Partial run: the requested start node is the sole entry point.
            if start_node_id is not None:
                return nid == start_node_id
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
                # A partial-run start node with no seeded predecessor falls back
                # to the trigger payload, like a root would.
                return trigger_payload if (is_root(nid) or nid == start_node_id) else None
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

        # ── partial run: only the start node + its downstream subgraph execute.
        # Everything else is pre-marked done and seeded with its latest persisted
        # output, so downstream expressions ($node.<id>.output…) keep resolving.
        if start_node_id is not None and start_node_id in nodes:
            reachable: set[str] = set()
            stack = [start_node_id]
            while stack:
                x = stack.pop()
                if x in reachable or x not in nodes:
                    continue
                reachable.add(x)
                stack.extend(e.target for e in outgoing.get(x, []))
            seed = seed_outputs or {}
            for nid in nodes:
                if nid in reachable:
                    continue
                done.add(nid)
                if nid in seed:
                    ctx["node"][nid] = {"output": seed[nid]}
            for e in edges:
                if e.source not in done:
                    continue
                # A seeded source feeding the live subgraph counts as a live edge
                # (its historical output becomes the target's input); everything
                # else upstream is dead.
                if e.target in reachable and e.source in ctx["node"]:
                    live_edges.add(e.id)
                else:
                    dead_edges.add(e.id)

        # ── resume (fase 2.4): a run interrupted by a crash/restart restarts from
        # its checkpoint — every node with a persisted {output, handles} entry is
        # marked done and its live/dead edges re-derived, so only the remaining
        # subgraph executes. Previously-skipped nodes stay skipped.
        if resume:
            persisted = await repo.get_run_context(db, run_id) or {}
            if persisted.get("trigger"):
                ctx["trigger"] = persisted["trigger"]
            for nid, entry in (persisted.get("node") or {}).items():
                if nid not in nodes or not isinstance(entry, dict):
                    continue
                ctx["node"][nid] = entry
                done.add(nid)
                handles = set(entry.get("handles") or ["main"])
                for e in outgoing[nid]:
                    (live_edges if e.sourceHandle in handles else dead_edges).add(e.id)
                if nodes[nid].type in _LOOP_TYPES:
                    body_ids, _ = loop_body(nid)
                    for b in body_ids:
                        done.add(b)
                        for e in outgoing[b]:
                            dead_edges.add(e.id)
            for nr in await repo.list_node_runs(db, run_id):
                if nr.status == "skipped" and nr.node_id in nodes and nr.node_id not in done:
                    skipped.add(nr.node_id)
                    for e in outgoing[nr.node_id]:
                        dead_edges.add(e.id)
            logger.info("Graph run %s resumed: %d/%d nodes already done", run_id, len(done), len(nodes))

        run_error: str | None = None
        comp_order: list[str] = []  # fase 16.3 — ok nodes in completion order
        node_order = {n.id: i for i, n in enumerate(graph.nodes)}

        async def run_compensations() -> str | None:
            """Fase 16.3 (saga) — after the run fails, walk the completed nodes in
            reverse order and execute the compensation subgraph hanging off each
            node's ``compensate`` handle, seeded with that node's own output. Only
            nodes that opted in (by wiring a ``compensate`` edge) participate, so
            existing graphs are unaffected. Returns a compound error string when a
            compensation itself fails, else None."""
            errors: list[str] = []
            for nid in reversed(comp_order):
                comp_targets = [e.target for e in outgoing.get(nid, [])
                                if e.sourceHandle == "compensate" and e.target in nodes]
                if not comp_targets:
                    continue
                # Forward closure of the compensation branch (a small DAG).
                sub: set[str] = set()
                stack = list(comp_targets)
                while stack:
                    x = stack.pop()
                    if x in sub or x not in nodes:
                        continue
                    sub.add(x)
                    stack.extend(e.target for e in outgoing.get(x, []))
                original_output = ctx["node"].get(nid, {}).get("output")
                produced: dict[str, object] = {}
                # Run the branch in graph order; each node's input is the output of
                # a preceding branch node feeding it, else the compensated node's
                # output (the roadmap's "compensation receives the original output").
                for cnid in sorted(sub, key=lambda x: node_order.get(x, 1 << 30)):
                    upstream = [e for e in incoming.get(cnid, []) if e.source in produced]
                    node_input = produced[upstream[0].source] if upstream else original_output
                    status, output, _handles, err = await _run_node(
                        db, run_id, profile_id, nodes[cnid], node_input, ctx, compensation=True,
                    )
                    produced[cnid] = output
                    if status == "error":
                        errors.append(f"compensation for '{nid}' failed at '{cnid}': {err}")
                        break
                await repo.set_run_status(db, run_id, "running", context=_ctx_snapshot(ctx))
            return "; ".join(errors) if errors else None

        def compute_runnable() -> list[str]:
            return [
                nid
                for nid in nodes
                if nid not in done
                and nid not in skipped
                and edges_resolved(nid)
                and (is_entry(nid) or has_live_input(nid))
            ]

        async def process_skips() -> list[str]:
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
            return newly_skipped

        def apply_outcome(nid: str, outcome) -> None:
            nonlocal run_error
            status, output, handles, err = outcome
            done.add(nid)
            if status == "ok":
                # Fase 16.3 — record the completion order of side-effecting nodes
                # so a later failure can compensate them in reverse.
                comp_order.append(nid)
                # `handles` is checkpointed with the output so a resumed run
                # (fase 2.4) can re-derive which outgoing edges were live.
                ctx["node"][nid] = {"output": output, "handles": list(handles)}
                active = set(handles)
                for e in outgoing[nid]:
                    (live_edges if e.sourceHandle in active else dead_edges).add(e.id)
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

        async def run_one(nid: str, input_override=_NO_OVERRIDE):
            node = nodes[nid]
            if node.type in _LOOP_TYPES:
                body_ids, entry_ids = loop_body(nid)
                return await _run_loop_node(
                    db, run_id, profile_id, node, nodes, incoming, outgoing,
                    ctx, primary_input(nid), body_ids, entry_ids,
                )
            if input_override is not _NO_OVERRIDE:
                node_input = input_override
            else:
                node_input = all_live_inputs(nid) if node.type == "merge" else primary_input(nid)
            return await _run_node(db, run_id, profile_id, node, node_input, ctx)

        paused = False
        if debug_mode:
            # Fase 8.3 — step debugger: run a single node per "step", or run until
            # the next breakpoint on "continue", then persist a `paused` checkpoint
            # and RETURN (the run is resumed by the next debug command). Reuses the
            # fase 2.4 resume machinery: this spawn was seeded from the checkpoint.
            dbg = await repo.get_run_debug(db, run_id) or {}
            breakpoints = set(dbg.get("breakpoints") or [])
            override = dbg.get("input", _NO_OVERRIDE) if "input" in dbg else _NO_OVERRIDE
            ran_one = False
            while True:
                newly_skipped = await process_skips()
                runnable = compute_runnable()
                if not runnable:
                    if newly_skipped:
                        continue
                    break  # nothing left → the run completed
                nid = min(runnable, key=lambda x: node_order.get(x, 1 << 30))
                if ran_one and (debug_mode == "step" or (debug_mode == "continue" and nid in breakpoints)):
                    node = nodes[nid]
                    pending_input = all_live_inputs(nid) if node.type == "merge" else primary_input(nid)
                    dbg["pending_node"] = nid
                    dbg.pop("input", None)
                    await repo.set_run_debug(db, run_id, dbg)
                    await repo.set_run_status(db, run_id, "paused", context=_ctx_snapshot(ctx))
                    _publish(run_id, {"kind": "run", "status": "paused",
                                      "pending_node": nid, "input": _preview(pending_input)})
                    paused = True
                    break
                outcome = await run_one(nid, override if not ran_one else _NO_OVERRIDE)
                ran_one = True
                apply_outcome(nid, outcome)
                await repo.set_run_status(db, run_id, "running", context=_ctx_snapshot(ctx))
                await repo.acquire_lease(db, run_id, _INSTANCE_ID, settings.graph_workflow_lease_ttl_seconds)
                if run_error:
                    break
        else:
            # Wave loop: run every runnable node in parallel until none remain.
            while True:
                newly_skipped = await process_skips()
                runnable = compute_runnable()
                if not runnable:
                    if newly_skipped:
                        continue  # skipping may unblock/close more nodes
                    break

                async def _wrap(nid: str):
                    async with node_semaphore:
                        return nid, await run_one(nid)

                results = await asyncio.gather(*(_wrap(nid) for nid in runnable), return_exceptions=True)

                for res in results:
                    if isinstance(res, Exception):
                        run_error = str(res)
                        logger.exception("Graph run %s: node crashed", run_id, exc_info=res)
                        continue
                    apply_outcome(*res)

                await repo.set_run_status(db, run_id, "running", context=_ctx_snapshot(ctx))
                await repo.acquire_lease(db, run_id, _INSTANCE_ID, settings.graph_workflow_lease_ttl_seconds)

                if run_error:
                    break

        if paused:
            logger.info("Graph run %s paused at %s (debug)", run_id, dbg.get("pending_node"))
            return

        if debug_mode:
            await repo.set_run_debug(db, run_id, None)  # session finished

        # Fase 16.3 — a failed run runs its saga compensations (reverse order)
        # before being finalized; a dry-run never performs real side effects.
        if run_error and not dry_run:
            try:
                comp_error = await run_compensations()
            except Exception as exc:  # noqa: BLE001 — compensation must not mask the run failure
                logger.exception("Graph run %s: compensation crashed", run_id)
                comp_error = f"compensation crashed: {exc}"
            if comp_error:
                run_error = f"{run_error} | compensation: {comp_error}"

        final_status = "failed" if run_error else "completed"
        await repo.set_run_status(db, run_id, final_status, context=_ctx_snapshot(ctx), error=run_error)
        _publish(run_id, {"kind": "run", "status": final_status, "error": run_error})
        _publish(run_id, {"kind": "done"})
        logger.info("Graph run %s finished: %s", run_id, final_status)
        if run_error:
            await repo.cancel_pending_approvals(db, run_id)
            await _maybe_alert_recurring_failures(db, run_id, profile_id)
            await _fire_error_triggers(db, run_id, run_error, trigger_type)
        else:
            await _fire_success_triggers(db, run_id, graph, ctx, trigger_type)
        # Fase 17.5 — buffer this outcome for the notification digest (opt-in).
        try:
            _fin_run = await repo.get_run(db, run_id)
            if _fin_run is not None:
                await _record_run_outcome(db, _fin_run.workflow_id, profile_id, run_id, final_status)
        except Exception:  # noqa: BLE001 — digest must never affect the run result
            logger.exception("run-outcome digest hook failed run=%s", run_id)

    except asyncio.CancelledError:
        await repo.set_run_status(db, run_id, "cancelled")
        await repo.cancel_pending_approvals(db, run_id)
        _publish(run_id, {"kind": "run", "status": "cancelled"})
        _publish(run_id, {"kind": "done"})
        raise
    except Exception as exc:  # noqa: BLE001 — a run failure must be recorded, not raised
        logger.exception("Graph run %s crashed", run_id)
        await repo.set_run_status(db, run_id, "failed", error=str(exc))
        await repo.cancel_pending_approvals(db, run_id)
        _publish(run_id, {"kind": "run", "status": "failed", "error": str(exc)})
        _publish(run_id, {"kind": "done"})
        await _fire_error_triggers(db, run_id, str(exc), trigger_type)
    finally:
        # Fase 2.3 — this run leaving its slot may allow a queued run to start.
        try:
            run = await repo.get_run(db, run_id)
            if run is not None:
                await _maybe_start_queued(db, run.workflow_id)
        except Exception:  # noqa: BLE001 — promotion must never mask the run outcome
            logger.exception("Graph run %s: queued-run promotion failed", run_id)
        try:
            await repo.release_lease(db, run_id, _INSTANCE_ID)
        except Exception:  # noqa: BLE001 — releasing must never mask the run outcome
            logger.exception("Graph run %s: lease release failed", run_id)
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


async def _maybe_start_queued(db: aiosqlite.Connection, workflow_id: str) -> None:
    """Promote queued runs of the workflow (FIFO) while slots are free (fase 2.3).
    Called when a run reaches a terminal state and at startup."""
    wf = await repo.get_workflow(db, workflow_id)
    if wf is None:
        return
    while True:
        limit = wf.max_concurrent_runs
        if limit > 0 and await repo.count_active_runs(db, workflow_id) >= limit:
            return
        queued = await repo.next_queued_run(db, workflow_id)
        if queued is None:
            return
        graph_json = await repo.get_run_graph(db, queued.id)
        try:
            graph = WorkflowGraph.model_validate(json.loads(graph_json or ""))
        except (ValueError, TypeError) as exc:
            await repo.set_run_status(db, queued.id, "failed", error=f"queued run has an invalid graph snapshot: {exc}")
            continue
        payload = ((await repo.get_run_context(db, queued.id)) or {}).get("trigger") or {}
        await repo.set_run_status(db, queued.id, "pending")
        _publish(queued.id, {"kind": "run", "status": "pending"})
        logger.info("Graph run %s promoted from queue (workflow %s)", queued.id, workflow_id)
        _spawn(queued.id, queued.profile_id, graph, queued.trigger_type, payload)


async def _fire_error_triggers(
    db: aiosqlite.Connection, run_id: str, error: str, failing_trigger_type: str
) -> None:
    """Fase 2.5 — fire every active workflow with a matching ``error`` trigger when
    a run fails, passing ``{workflow_id, workflow_name, run_id, error, failed_node}``
    as ``$trigger``. Runs that were themselves started by an error trigger never
    cascade (loop guard); a workflow never reacts to its own failures. Best-effort:
    a broken handler workflow must not disturb the failing run's bookkeeping."""
    if failing_trigger_type == "error":
        return
    try:
        run = await repo.get_run(db, run_id)
        if run is None:
            return
        triggers = await repo.list_error_triggers(db, run.workflow_id)
        if not triggers:
            return
        wf = await repo.get_workflow(db, run.workflow_id)
        payload = {
            "workflow_id": run.workflow_id,
            "workflow_name": wf.name if wf else None,
            "run_id": run_id,
            "error": error,
            "failed_node": await repo.first_error_node(db, run_id),
        }
        for row in triggers:
            try:
                await run_workflow(
                    db, row["workflow_id"], row["wf_profile_id"],
                    trigger_type="error", trigger_payload=payload,
                )
                await _note_trigger_success(db, row["id"])
            except Exception as exc:  # noqa: BLE001
                logger.exception("error trigger firing failed id=%s", row.get("id"))
                await _note_trigger_failure(
                    db, row["id"], row["workflow_id"], row["wf_profile_id"], str(exc)
                )
    except Exception:  # noqa: BLE001
        logger.exception("error-trigger dispatch failed for run %s", run_id)


def _sink_output(graph: WorkflowGraph, node_ctx: dict) -> object:
    """The output of the graph's sink node(s) — a single value when there is one
    sink, else ``{sink_id: output}``. Shared by the subworkflow return value and
    the success-trigger payload (fase 6.1)."""
    sources = {e.source for e in graph.edges}
    sinks = [n.id for n in graph.nodes if n.id not in sources]
    if len(sinks) == 1:
        return node_ctx.get(sinks[0], {}).get("output")
    return {s: node_ctx.get(s, {}).get("output") for s in sinks}


async def _fire_success_triggers(
    db: aiosqlite.Connection, run_id: str, graph: WorkflowGraph, ctx: dict, completed_trigger_type: str
) -> None:
    """Fase 6.1 — fire every active workflow with a matching ``success`` trigger
    when a run completes, passing ``{workflow_id, workflow_name, run_id, output}``
    as ``$trigger`` (output = the completed run's sink output). Same anti-loop
    guards as the error trigger: a success-triggered run never cascades, and a
    workflow never reacts to its own completions."""
    if completed_trigger_type == "success":
        return
    try:
        run = await repo.get_run(db, run_id)
        if run is None:
            return
        triggers = await repo.list_success_triggers(db, run.workflow_id)
        if not triggers:
            return
        wf = await repo.get_workflow(db, run.workflow_id)
        payload = {
            "workflow_id": run.workflow_id,
            "workflow_name": wf.name if wf else None,
            "run_id": run_id,
            "output": _jsonable(_sink_output(graph, ctx.get("node") or {})),
        }
        for row in triggers:
            try:
                await run_workflow(
                    db, row["workflow_id"], row["wf_profile_id"],
                    trigger_type="success", trigger_payload=payload,
                )
                await _note_trigger_success(db, row["id"])
            except Exception as exc:  # noqa: BLE001
                logger.exception("success trigger firing failed id=%s", row.get("id"))
                await _note_trigger_failure(
                    db, row["id"], row["workflow_id"], row["wf_profile_id"], str(exc)
                )
    except Exception:  # noqa: BLE001 — best-effort, must not disturb the completed run
        logger.exception("success-trigger dispatch failed for run %s", run_id)


async def resume_interrupted_runs() -> None:
    """Fase 2.4 — called once at startup: resume every run left 'running'/'pending'
    by a crash/restart from its checkpointed context (each run re-executes its own
    graph snapshot), and re-evaluate queued runs whose slot may now be free."""
    db = await _connect()
    try:
        runs = await repo.list_interrupted_runs(db)
        queued_workflows: list[str] = []
        resumed = 0
        for run in runs:
            if run.status == "queued":
                if run.workflow_id not in queued_workflows:
                    queued_workflows.append(run.workflow_id)
                continue
            graph_json = await repo.get_run_graph(db, run.id)
            try:
                graph = WorkflowGraph.model_validate(json.loads(graph_json or ""))
            except (ValueError, TypeError) as exc:
                await repo.set_run_status(db, run.id, "failed", error=f"resume: invalid graph snapshot: {exc}")
                continue
            await repo.fail_running_node_runs(db, run.id, "interrupted by restart")
            payload = ((await repo.get_run_context(db, run.id)) or {}).get("trigger") or {}
            _spawn(run.id, run.profile_id, graph, run.trigger_type, payload, resume=True)
            resumed += 1
        for wf_id in queued_workflows:
            await _maybe_start_queued(db, wf_id)
        if resumed or queued_workflows:
            logger.info(
                "workflow_graph_service: resumed %d interrupted run(s), re-evaluated %d workflow queue(s)",
                resumed, len(queued_workflows),
            )
    finally:
        await db.close()


def _ctx_snapshot(ctx: dict) -> dict:
    snapshot = {"node": ctx["node"], "trigger": ctx.get("trigger")}
    if ctx.get("dry_effects"):
        snapshot["dry_effects"] = ctx["dry_effects"]
    return snapshot


_EXTERNAL_EFFECT_TYPES = frozenset({"http.request", "db.query"})
# ``custom.`` (fase 19): a custom node is a declarative http.request or a
# sandboxed python module — both real side effects, so pins/dry-run intercept it.
# ``telegram.`` (fase 20): outbound bot messages are a real side effect too.
_EXTERNAL_EFFECT_PREFIXES = ("llm.", "notification.", "email.", "custom.", "telegram.")


def _is_external_effect(node_type: str) -> bool:
    """Fase 11.1/11.2 — node types with a real-world side effect (an outbound
    HTTP call, a DB write/read, a sent notification/email, a paid LLM call).
    The only types ``_mock_dispatch`` will ever intercept."""
    return node_type in _EXTERNAL_EFFECT_TYPES or node_type.startswith(_EXTERNAL_EFFECT_PREFIXES)


def _dry_run_placeholder(node_type: str) -> dict:
    """Fase 11.2 — a typed stand-in for an external-effect node's output when it
    has no pin, so downstream expressions still resolve to something shaped
    like the real thing instead of erroring on a missing field."""
    if node_type == "http.request":
        return {"status": 200, "headers": {}, "body": {}, "_mocked": True}
    if node_type == "db.query":
        return {"rows": [], "row_count": 0, "_mocked": True}
    if node_type in ("llm.completion", "llm.agent"):
        return {
            "text": "[dry-run: llm call skipped]",
            "_usage": {"tokens_in": 0, "tokens_out": 0, "tokens_total": 0},
            "_mocked": True,
        }
    if node_type == "llm.classify":
        return {"label": "", "confidence": 0.0, "_mocked": True}
    if node_type == "llm.extract":
        return {"data": {}, "_mocked": True}
    if node_type == "llm.judge":
        return {"score": 0, "verdict": "pass", "passed": True, "_mocked": True}
    if node_type.startswith("notification.") or node_type.startswith("email."):
        return {"sent": True, "_mocked": True}
    return {"_mocked": True}


def _mock_dispatch(node: GraphNode, ctx: dict) -> tuple[object, list[str], str] | None:
    """Fase 11.1/11.2 — when this returns non-None, ``_run_node`` uses it
    instead of calling ``_dispatch``: ``(output, handles, source)`` where
    ``source`` is ``pin`` (the node's fase 3.2 pinned output was used) or
    ``placeholder`` (a typed dry-run stand-in). Only ever intercepts
    external-effect node types (see ``_is_external_effect``); every other node
    always executes for real, pinned or not, so branch/logic nodes keep
    routing correctly. A test-suite run (``_use_pins``) only mocks nodes that
    actually carry a pin — everything else still makes the real call. A
    dry-run (``_dry_run``) mocks every external-effect node unconditionally."""
    if not _is_external_effect(node.type):
        return None
    if node.pinnedOutput is not None and (ctx.get("_use_pins") or ctx.get("_dry_run")):
        return node.pinnedOutput, ["main"], "pin"
    if ctx.get("_dry_run"):
        return _dry_run_placeholder(node.type), ["main"], "placeholder"
    return None


def _mask_json_path(value, path: str):
    """Fase 12.2 — a deep copy of ``value`` with the leaf at dotted ``path``
    replaced by ``"***"``. Missing/non-traversable paths are a no-op (the
    output shape may legitimately vary run to run)."""
    parts = [p for p in path.split(".") if p]
    if not parts:
        return value
    out = json.loads(json.dumps(value, default=str)) if isinstance(value, (dict, list)) else value
    cur = out
    for part in parts[:-1]:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return out
        else:
            return out
    last = parts[-1]
    if isinstance(cur, dict) and last in cur:
        cur[last] = "***"
    elif isinstance(cur, list):
        try:
            cur[int(last)] = "***"
        except (ValueError, IndexError):
            pass
    return out


def _redact_output(output, paths: list[str]):
    """Fase 12.2 — the persisted/streamed/exported view of a node's output with
    every configured field masked. The *returned* value from ``_run_node`` is
    always the real one, so downstream nodes keep resolving it in cleartext —
    only what reaches storage, SSE and exports goes through this."""
    if not paths:
        return output
    redacted = output
    for path in paths:
        redacted = _mask_json_path(redacted, path)
    return redacted


async def _run_node(
    db: aiosqlite.Connection,
    run_id: str,
    profile_id: str,
    node: GraphNode,
    node_input,
    ctx: dict,
    compensation: bool = False,
) -> tuple[str, object, list[str], str | None]:
    """Execute one node with retry/backoff. Returns (status, output, handles, error).

    ``compensation`` (fase 16.3) tags the live SSE frames so the run panel can
    render a saga rollback distinctly from the forward run; it does not change
    execution."""
    from app.services import expression_resolver

    def _frame(**extra):
        return {"kind": "node", "node_id": node.id, **({"compensation": True} if compensation else {}), **extra}

    nr_id = await repo.start_node_run(db, run_id, node.id, node.type, node_input)
    _publish(run_id, _frame(status="running"))

    mock = _mock_dispatch(node, ctx)
    if mock is not None:
        output, handles, source = mock
        if ctx.get("_dry_run"):
            ctx.setdefault("dry_effects", []).append(
                {"node_id": node.id, "node_type": node.type, "source": source}
            )
        persisted = _redact_output(output, node.redact)
        await repo.finish_node_run(db, nr_id, "ok", output=persisted)
        _publish(run_id, _frame(status="ok", output=_preview(persisted)))
        return "ok", output, handles, None

    local_ctx = {**ctx, "json": node_input}
    attempts = node.retry + 1
    last_err: str | None = None
    timeout_s = node.timeoutMs / 1000.0 if node.timeoutMs > 0 else None

    # Fase 18.2 — pick the A/B variant once per node run (not per retry, so the
    # round-robin counter advances once) and record it on the output so per-node
    # metrics can break results down by variant.
    variant = await _select_variant(db, ctx.get("_workflow_id"), node)
    variant_name = variant[0] if variant else None
    raw_params = variant[1] if variant else node.params

    for attempt in range(attempts):
        try:
            params = await expression_resolver.resolve_params(raw_params, local_ctx)
            dispatch = _dispatch(db, profile_id, node, node_input, params, local_ctx)
            if timeout_s is not None:
                try:
                    output, handles = await asyncio.wait_for(dispatch, timeout_s)
                except asyncio.TimeoutError:
                    raise TimeoutError(f"node timed out after {node.timeoutMs} ms")
            else:
                output, handles = await dispatch
            if variant_name and isinstance(output, dict):
                output["_variant"] = variant_name
            persisted = _redact_output(output, node.redact)
            await repo.finish_node_run(db, nr_id, "ok", output=persisted)
            _publish(run_id, _frame(status="ok", output=_preview(persisted)))
            return "ok", output, handles, None
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            logger.warning("Graph node %s attempt %d/%d failed: %s", node.id, attempt + 1, attempts, exc)
            if attempt + 1 < attempts and node.backoff:
                delay = node.backoff
                if node.backoffStrategy == "exponential":
                    delay = node.backoff * (2 ** attempt)
                await asyncio.sleep(min(delay, _RETRY_MAX_BACKOFF_SECONDS))

    if node.continueOnFail or node.onError == "continue":
        await repo.finish_node_run(db, nr_id, "ok", output={"error": last_err}, error=last_err)
        _publish(run_id, _frame(status="ok", output={"error": last_err}))
        return "ok", {"error": last_err}, ["main"], None

    if node.onError == "branch":
        # Route the failure through the dedicated 'error' handle: the node run is
        # recorded as an error, but the run continues down the error branch with
        # {error, input} as payload (edges on 'main' go dead → their targets skip).
        output = {"error": last_err, "input": node_input}
        await repo.finish_node_run(db, nr_id, "error", output=output, error=last_err)
        _publish(run_id, _frame(status="error", error=last_err))
        return "ok", output, ["error"], None

    await repo.finish_node_run(db, nr_id, "error", error=last_err)
    _publish(run_id, _frame(status="error", error=last_err))
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
    """Run a ``for``/``repeat``/``while`` node: execute the body subgraph once per
    iteration (``$item``/``$index`` in scope), collect the results, then continue
    on ``done``. Returns the same (status, output, handles, error) tuple as
    ``_run_node``. ``while`` (fase 6.3) re-evaluates its condition before every
    iteration — ``$item`` is the previous iteration's body output (the node input
    on the first pass) — under a mandatory iteration cap."""
    from app.services import expression_resolver

    nr_id = await repo.start_node_run(db, run_id, node.id, node.type, node_input)
    _publish(run_id, {"kind": "node", "node_id": node.id, "status": "running"})
    try:
        local_ctx = {**ctx, "json": node_input}

        if node.type == "while":
            # The condition references $item/$index, so params are re-resolved
            # per iteration with those in scope — never up front like for/repeat.
            first = await expression_resolver.resolve_params(
                node.params, {**local_ctx, "item": node_input, "index": 0}
            )
            cap = int(first.get("maxIterations") or 100)
            cap = max(0, min(cap, settings.graph_workflow_while_max_iterations, _MAX_LOOP_ITERATIONS))
            results = []
            item = node_input
            for idx in range(cap):
                iter_ctx = {**ctx, "item": item, "index": idx}
                cond_params = first if idx == 0 else await expression_resolver.resolve_params(
                    node.params, {**iter_ctx, "json": node_input}
                )
                if not _as_bool(cond_params.get("condition")):
                    break
                out = await _run_body_once(
                    db, run_id, profile_id, node, nodes, incoming, outgoing,
                    body_ids, entry_ids, iter_ctx, item,
                )
                results.append(out)
                item = out  # next condition/iteration sees this pass's output as $item
            else:
                if cap > 0:
                    logger.warning("Graph while node %s hit its iteration cap (%d)", node.id, cap)
            output = {"items": results, "count": len(results), "capped": len(results) == cap and cap > 0}
            await repo.finish_node_run(db, nr_id, "ok", output=output)
            _publish(run_id, {"kind": "node", "node_id": node.id, "status": "ok", "output": _preview(output)})
            return "ok", output, ["done"], None

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
    # Per-iteration node map: body nodes already executed in THIS iteration are
    # addressable as $node.<id>.output downstream in the body (the paths the
    # editor's field chooser produces), without leaking into other iterations
    # or the main graph.
    ctx = {**ctx, "node": dict(ctx.get("node") or {})}

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
                ctx["node"][nid] = {"output": output}
            else:
                raise RuntimeError(err or f"loop body node {nid} failed")

    # Body result: the output of sink nodes (no outgoing edge inside the body).
    sinks = [nid for nid in body_ids if not any(e.target in body_ids for e in outgoing[nid])]
    if len(sinks) == 1:
        return outputs.get(sinks[0])
    return {s: outputs.get(s) for s in sinks}


# ── node executors ──────────────────────────────────────────────────────────

async def _dispatch_stateless(node_type: str, params: dict, node_input, ctx: dict | None = None) -> tuple[object, list[str]]:
    """The ``_REMOTE_CAPABLE_TYPES`` subset of ``_dispatch`` — needs no ``db``/
    ``profile_id`` (no vault, tool registry or workspace-storage access), so
    it runs identically in the backend process (14.1's local fallback) or a
    remote runner agent executing a claimed job (``app.runner.agent``)."""
    ctx = ctx or {}
    if node_type == "set":
        fields = params.get("fields")
        return (fields if isinstance(fields, dict) else params), ["main"]
    if node_type == "if":
        cond = _as_bool(params.get("condition"))
        return {"value": cond, "input": node_input}, ["true" if cond else "false"]
    if node_type == "switch":
        return _exec_switch(None, params, node_input)
    if node_type == "merge":
        items = node_input if isinstance(node_input, list) else [node_input]
        return {"items": items}, ["main"]
    if node_type == "filter":
        return _exec_filter(params, node_input), ["main"]
    if node_type == "code":
        return await _exec_code(params, node_input, ctx), ["main"]
    if node_type == "wait":
        return await _exec_wait(params), ["main"]
    if node_type == "aggregate":
        return _exec_aggregate(params, node_input), ["main"]
    if node_type == "batch":
        return _exec_batch(params, node_input), ["main"]
    if node_type == "http.request":
        return await _exec_http_request(params), ["main"]
    if node_type == "db.query":
        return await _exec_db_query(params), ["main"]
    if node_type == "queue.publish":
        return await _exec_queue_publish(params, node_input), ["main"]
    raise ValueError(f"node type '{node_type}' cannot execute without a backend context")


async def _dispatch_remote(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, node_input, params: dict, ctx: dict,
) -> tuple[object, list[str]] | None:
    """Fase 14.1 — hand this node's execution to a matching online remote
    runner. Returns ``None`` when the node's type is not in
    ``_REMOTE_CAPABLE_TYPES`` (needs backend context the runner never gets —
    ``runOn`` is silently ignored for those). Otherwise either returns the
    runner's result or raises like any other dispatch failure (subject to the
    node's own retry/onError) — UNLESS ``runOnFallback == 'local'``, in which
    case a missing/timed-out runner falls back to ``_dispatch_stateless``
    right here instead of failing the node."""
    if node.type not in _REMOTE_CAPABLE_TYPES:
        return None

    label = node.runOn
    timeout_s = (node.timeoutMs / 1000.0) if node.timeoutMs > 0 else float(settings.graph_workflow_runner_job_timeout)

    async def _local_fallback(reason: str) -> tuple[object, list[str]]:
        if node.runOnFallback == "local":
            logger.warning("Graph node %s: %s — falling back to local execution", node.id, reason)
            return await _dispatch_stateless(node.type, params, node_input, ctx)
        raise RuntimeError(reason)

    candidates = await repo.find_online_runners(
        db, profile_id, label, node.type,
        heartbeat_timeout=settings.graph_workflow_runner_heartbeat_timeout,
    )
    if not candidates:
        return await _local_fallback(f"no online runner labelled '{label}' allows node type '{node.type}'")

    runner = candidates[0]
    job_id = await repo.create_runner_job(
        db, runner["id"], ctx.get("_run_id"), node.id, node.type,
        {"params": params, "input": node_input},
    )
    deadline = time.time() + timeout_s
    poll = max(0.1, settings.graph_workflow_runner_poll_interval)
    while time.time() < deadline:
        await asyncio.sleep(poll)
        row = await repo.get_runner_job(db, job_id)
        if row is None:
            break
        if row["status"] == "done":
            result = json.loads(row["result_json"] or "{}")
            return result.get("output"), list(result.get("handles") or ["main"])
        if row["status"] == "failed":
            raise RuntimeError(row["error"] or f"runner job {job_id} failed")
    await repo.timeout_runner_job(db, job_id)
    return await _local_fallback(f"runner job {job_id} on '{runner['name']}' timed out after {timeout_s:.0f}s")


async def _dispatch(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, node_input, params: dict, ctx: dict
) -> tuple[object, list[str]]:
    """Return (output, active_output_handles) for a node.

    Cross-cutting remote routing stays here; per-type execution is delegated to
    the dispatch table (app/workflow/registry.py, roadmap §4.1). The handlers
    are registered just below in this module.
    """
    if node.runOn:
        # Fase 14.1 — route to a remote runner when the node's type can
        # execute without db/profile_id context; None means "not remote-
        # capable, ignore runOn and execute here" (e.g. tool.*/llm.*/subworkflow).
        remote = await _dispatch_remote(db, profile_id, node, node_input, params, ctx)
        if remote is not None:
            return remote

    return await registry.resolve(
        DispatchCtx(
            db=db, profile_id=profile_id, node=node,
            node_input=node_input, params=params, ctx=ctx,
        )
    )


# ── Node handler registry ────────────────────────────────────────────────────
# One handler per node type / family, registered into the dispatch table. Each
# adapter unpacks DispatchCtx into the existing _exec_* implementation and
# returns (output, active_output_handles) — behaviour identical to the former
# if/elif chain. Grouped by family so a family can later move wholesale into
# app/workflow/nodes/<family>.py (the P1 explosion) without touching the engine.

# -- triggers & core control flow --

@registry.node(*_TRIGGER_TYPES)
async def _h_trigger(c: DispatchCtx):
    return c.node_input if c.node_input is not None else c.params, ["main"]


@registry.node("tool.", prefix=True)
async def _h_tool(c: DispatchCtx):
    return await _exec_tool(c.profile_id, c.ntype[len("tool."):], c.params), ["main"]


@registry.node("set")
async def _h_set(c: DispatchCtx):
    fields = c.params.get("fields")
    return (fields if isinstance(fields, dict) else c.params), ["main"]


@registry.node("if")
async def _h_if(c: DispatchCtx):
    cond = _as_bool(c.params.get("condition"))
    return {"value": cond, "input": c.node_input}, ["true" if cond else "false"]


@registry.node("switch")
async def _h_switch(c: DispatchCtx):
    return _exec_switch(c.node, c.params, c.node_input)


@registry.node("merge")
async def _h_merge(c: DispatchCtx):
    items = c.node_input if isinstance(c.node_input, list) else [c.node_input]
    return {"items": items}, ["main"]


@registry.node("filter")
async def _h_filter(c: DispatchCtx):
    return _exec_filter(c.params, c.node_input), ["main"]


@registry.node("code")
async def _h_code(c: DispatchCtx):
    return await _exec_code(c.params, c.node_input, c.ctx), ["main"]


@registry.node("wait")
async def _h_wait(c: DispatchCtx):
    return await _exec_wait(c.params), ["main"]


@registry.node("aggregate")
async def _h_aggregate(c: DispatchCtx):
    return _exec_aggregate(c.params, c.node_input), ["main"]


@registry.node("batch")
async def _h_batch(c: DispatchCtx):
    return _exec_batch(c.params, c.node_input), ["main"]


# -- data / io --

@registry.node("http.request")
async def _h_http_request(c: DispatchCtx):
    return await _exec_http_request(c.params), ["main"]


@registry.node("subworkflow")
async def _h_subworkflow(c: DispatchCtx):
    return await _exec_subworkflow(c.db, c.profile_id, c.params, c.node_input, c.ctx), ["main"]


@registry.node("workflow.", prefix=True)
async def _h_workflow_call(c: DispatchCtx):
    # Fase 6.4 — a "callable" workflow exposed as a typed catalog node: the
    # node's params ARE the child's input payload (validated against its
    # input contract by _exec_subworkflow).
    return await _exec_subworkflow(
        c.db, c.profile_id,
        {"workflow_id": c.ntype[len("workflow."):], "payload": c.params},
        c.node_input, c.ctx,
    ), ["main"]


@registry.node("chat.reply")
async def _h_chat_reply(c: DispatchCtx):
    # Fase 9.3 — terminal reply node: its text is the conversation answer.
    text = c.params.get("text")
    if text in (None, ""):
        text = c.node_input
    if not isinstance(text, str):
        text = json.dumps(text, default=str, ensure_ascii=False)
    return {"reply": text}, ["main"]


@registry.node("kb.search")
async def _h_kb_search(c: DispatchCtx):
    return await _exec_kb_search(c.db, c.profile_id, c.params, c.node_input), ["main"]


@registry.node("db.query")
async def _h_db_query(c: DispatchCtx):
    return await _exec_db_query(c.params), ["main"]


@registry.node("file.read")
async def _h_file_read(c: DispatchCtx):
    return await _exec_file_read(c.params), ["main"]


@registry.node("file.write")
async def _h_file_write(c: DispatchCtx):
    return await _exec_file_write(c.params, c.node_input), ["main"]


@registry.node("file.parse")
async def _h_file_parse(c: DispatchCtx):
    return _exec_file_parse(c.params, c.node_input), ["main"]


# -- notifications & Telegram channel (Phase 52, roadmap fase 20) --

@registry.node("notify.telegram")
async def _h_notify_telegram(c: DispatchCtx):
    return await _exec_notify_telegram(c.db, c.profile_id, c.params), ["main"]


@registry.node("telegram.send")
async def _h_telegram_send(c: DispatchCtx):
    return await _exec_telegram_send(c.params, c.node_input, c.ctx), ["main"]


@registry.node("telegram.sendMedia")
async def _h_telegram_send_media(c: DispatchCtx):
    return await _exec_telegram_send_media(c.params, c.ctx), ["main"]


@registry.node("telegram.editMessage")
async def _h_telegram_edit(c: DispatchCtx):
    return await _exec_telegram_edit(c.params, c.ctx), ["main"]


@registry.node("telegram.deleteMessage")
async def _h_telegram_delete(c: DispatchCtx):
    return await _exec_telegram_delete(c.params, c.ctx), ["main"]


@registry.node("telegram.ask")
async def _h_telegram_ask(c: DispatchCtx):
    return await _exec_telegram_ask(c.db, c.profile_id, c.node, c.params, c.ctx)


@registry.node("notify.email")
async def _h_notify_email(c: DispatchCtx):
    return await _exec_notify_email(c.params), ["main"]


@registry.node("notify.webhook")
async def _h_notify_webhook(c: DispatchCtx):
    return await _exec_notify_webhook(c.params, c.node_input), ["main"]


@registry.node("notify.inapp")
async def _h_notify_inapp(c: DispatchCtx):
    return await _exec_notify_inapp(c.db, c.profile_id, c.params), ["main"]


# -- LLM nodes --

@registry.node("llm.completion")
async def _h_llm_completion(c: DispatchCtx):
    return await _exec_llm_completion(c.db, c.profile_id, c.params), ["main"]


@registry.node("llm.agent")
async def _h_llm_agent(c: DispatchCtx):
    return await _exec_llm_agent(c.db, c.profile_id, c.params), ["main"]


@registry.node("llm.classify")  # Phase 35 (roadmap fase 4)
async def _h_llm_classify(c: DispatchCtx):
    return await _exec_llm_classify(c.db, c.profile_id, c.params, c.node_input), ["main"]


@registry.node("llm.extract")
async def _h_llm_extract(c: DispatchCtx):
    return await _exec_llm_extract(c.db, c.profile_id, c.params, c.node_input), ["main"]


@registry.node("llm.judge")  # Phase 50 (roadmap fase 18.1 — LLM quality gate)
async def _h_llm_judge(c: DispatchCtx):
    return await _exec_llm_judge(c.db, c.profile_id, c.params, c.node_input)


# -- human-in-the-loop & async waits --

@registry.node("human.approval")
async def _h_human_approval(c: DispatchCtx):
    return await _exec_human_approval(c.db, c.profile_id, c.node, c.params, c.ctx)


@registry.node("human.input")  # Phase 42 (roadmap fase 10)
async def _h_human_input(c: DispatchCtx):
    return await _exec_human_input(c.db, c.profile_id, c.node, c.params, c.ctx)


@registry.node("wait.event")
async def _h_wait_event(c: DispatchCtx):
    return await _exec_wait_event(c.db, c.profile_id, c.node, c.params, c.ctx)


@registry.node("queue.publish")
async def _h_queue_publish(c: DispatchCtx):
    return await _exec_queue_publish(c.params, c.node_input), ["main"]


# -- connectors & multimodal (Phase 47, roadmap fase 15) --

@registry.node("connector.", prefix=True)
async def _h_connector(c: DispatchCtx):
    # 15.1 — curated integration over http.request; auth from $secrets is
    # already resolved into params by the expression layer.
    return await _exec_connector(c.ntype[len("connector."):], c.params, c.node_input), ["main"]


@registry.node("ssh.exec")
async def _h_ssh_exec(c: DispatchCtx):
    return await _exec_ssh_exec(c.params), ["main"]


@registry.node("browser")
async def _h_browser(c: DispatchCtx):
    return await _exec_browser(c.params), ["main"]


@registry.node("doc.convert")
async def _h_doc_convert(c: DispatchCtx):
    return await _exec_doc_convert(c.params, c.node_input), ["main"]


# -- persistent state (Phase 48, roadmap fase 16.1) --

@registry.node("state.get", "state.set", "state.increment")
async def _h_state(c: DispatchCtx):
    return await _exec_state(c.db, c.ctx.get("_workflow_id"), c.ntype, c.params, c.node_input), ["main"]


# -- Custom Node SDK (Phase 51, roadmap fase 19) --

@registry.node("custom.", prefix=True)
async def _h_custom(c: DispatchCtx):
    from app.services import custom_node_service

    return await custom_node_service.execute(
        c.db, c.profile_id, c.ntype, c.params, c.node_input, c.ctx
    )


async def _exec_state(
    db: aiosqlite.Connection, workflow_id: str | None, ntype: str, params: dict, node_input,
) -> dict:
    """Fase 16.1 — per-workflow persistent key/value state that outlives a run.

    ``state.get`` → ``{key, value, found}``; ``state.set`` → ``{key, value}``;
    ``state.increment`` → ``{key, value}`` (numeric, atomic). ``ttlSeconds`` on
    set/increment gives the key an expiry (default from settings). Backed by the
    ``workflow_state`` table, never carried in an export."""
    if not workflow_id:
        raise ValueError("state.* nodes require a workflow context (not available in this run mode)")
    key = str(params.get("key") or "").strip()
    if not key:
        raise ValueError("state node requires a non-empty 'key'")

    if ntype == "state.get":
        found, value = await repo.state_get(db, workflow_id, key)
        if not found and "default" in params:
            value = params.get("default")
        return {"key": key, "value": value, "found": found}

    ttl = params.get("ttlSeconds")
    ttl = int(ttl) if isinstance(ttl, (int, float)) and not isinstance(ttl, bool) else None
    if ttl is None and settings.graph_workflow_state_default_ttl_seconds > 0:
        ttl = settings.graph_workflow_state_default_ttl_seconds

    if ntype == "state.set":
        # `value` defaults to the node's primary input, so `state.set` can park
        # whatever flowed in without an explicit expression.
        value = params["value"] if "value" in params else node_input
        await repo.state_set(db, workflow_id, key, value, ttl)
        return {"key": key, "value": value}

    # state.increment
    amount = params.get("amount", 1)
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        amount = 1
    value = await repo.state_increment(db, workflow_id, key, amount, ttl)
    return {"key": key, "value": value}


# ── message queue (Phase 46 — roadmap fase 14.4) ────────────────────────────

class QueueDriver:
    """Pluggable interface behind ``queue.publish``/``queue.consume``. A real
    broker adapter (RabbitMQ/AMQP, Kafka, MQTT — one connector at a time, per
    the roadmap) implements ``publish``/``consume`` against its client library
    and is selected the same way as the two shipped drivers below; nothing
    else in the engine (the node, the trigger poll) needs to change."""

    async def publish(self, topic: str, message: object, headers: dict) -> None:
        raise NotImplementedError

    async def consume(self, topic: str, limit: int = 10) -> list[dict]:
        """Up to ``limit`` pending messages as ``[{id, topic, message,
        headers}]``, already claimed (won't be redelivered by this driver)."""
        raise NotImplementedError


class _MemoryQueueDriver(QueueDriver):
    """Per-process, in-memory (``GRAPH_WORKFLOW_QUEUE_DRIVER=memory``) — lost on
    restart, zero setup. Good for tests and single-process dev."""

    def __init__(self) -> None:
        self._topics: dict[str, list[dict]] = {}

    async def publish(self, topic: str, message: object, headers: dict) -> None:
        self._topics.setdefault(topic, []).append(
            {"id": str(uuid.uuid4()), "message": message, "headers": headers or {}}
        )

    async def consume(self, topic: str, limit: int = 10) -> list[dict]:
        items = self._topics.get(topic, [])
        claimed, self._topics[topic] = items[:limit], items[limit:]
        return [
            {"id": it["id"], "topic": topic, "message": it["message"], "headers": it["headers"]}
            for it in claimed
        ]


class _DbQueueDriver(QueueDriver):
    """Persisted in ``workflow_queue_messages`` (``GRAPH_WORKFLOW_QUEUE_DRIVER=db``,
    the default) — survives restarts, still no external broker."""

    async def publish(self, topic: str, message: object, headers: dict) -> None:
        db = await _connect()
        try:
            payload = message if isinstance(message, (dict, list)) else {"value": message}
            await repo.publish_queue_message(db, topic, payload, headers or {})
        finally:
            await db.close()

    async def consume(self, topic: str, limit: int = 10) -> list[dict]:
        db = await _connect()
        try:
            return await repo.consume_queue_messages(db, topic, limit)
        finally:
            await db.close()


_memory_queue_driver = _MemoryQueueDriver()
_db_queue_driver = _DbQueueDriver()


def get_queue_driver() -> QueueDriver:
    return _memory_queue_driver if settings.graph_workflow_queue_driver == "memory" else _db_queue_driver


async def _exec_queue_publish(params: dict, node_input) -> dict:
    topic = str(params.get("topic") or "").strip()
    if not topic:
        raise ValueError("queue.publish: 'topic' is required")
    message = params.get("message")
    if message is None:
        message = node_input
    headers = params.get("headers") if isinstance(params.get("headers"), dict) else {}
    await get_queue_driver().publish(topic, message, headers)
    return {"topic": topic, "published": True}


_QUEUE_MAX_FIRES_PER_POLL = 20  # runs fired per queue.consume poll pass


async def _poll_queue_consume(db: aiosqlite.Connection, row: dict, cfg: dict) -> bool:
    """One ``queue.consume`` poll pass: drain up to ``batch_size`` pending
    messages of the configured topic off the active QueueDriver and fire one
    run per message, ``$trigger = {message, topic, headers}``."""
    topic = str(cfg.get("topic") or "").strip()
    if not topic:
        return False
    limit = max(1, min(int(cfg.get("batch_size") or 10), _QUEUE_MAX_FIRES_PER_POLL))
    messages = await get_queue_driver().consume(topic, limit)
    fired = False
    for msg in messages:
        await run_workflow(
            db, row["workflow_id"], row["wf_profile_id"],
            trigger_type="queue.consume",
            trigger_payload={"message": msg.get("message"), "topic": topic, "headers": msg.get("headers") or {}},
        )
        fired = True
    return fired


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


# ── per-host rate limiting (fase 6.6) ───────────────────────────────────────
# Sliding-window admission timestamps per host, shared by every run in the
# process. Requests over the threshold WAIT (they don't fail); the wait is
# reported in the node output as `rate_limited_s`.
_rate_hits: dict[str, list[float]] = {}
_rate_lock: asyncio.Lock = asyncio.Lock()
_global_rate_limits: dict[str, int] | None = None  # parsed lazily from settings


def _parse_rate_limits(raw: str) -> dict[str, int]:
    """GRAPH_WORKFLOW_RATE_LIMITS: a JSON object {host: rpm} or 'host=rpm'
    pairs separated by commas. Invalid entries are dropped."""
    text = (raw or "").strip()
    out: dict[str, int] = {}
    if not text:
        return out
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return out
        if isinstance(data, dict):
            for host, rpm in data.items():
                try:
                    out[str(host).lower()] = max(1, int(rpm))
                except (TypeError, ValueError):
                    continue
        return out
    for pair in text.split(","):
        host, _, rpm = pair.partition("=")
        try:
            out[host.strip().lower()] = max(1, int(rpm))
        except (TypeError, ValueError):
            continue
    return {h: r for h, r in out.items() if h}


def _host_rate_limit(host: str, node_rpm) -> int | None:
    """The effective requests-per-minute cap for a host: the stricter of the
    node's own maxRequestsPerMinute and the global per-domain map (None = no cap)."""
    global _global_rate_limits
    if _global_rate_limits is None:
        _global_rate_limits = _parse_rate_limits(settings.graph_workflow_rate_limits)
    caps: list[int] = []
    global_cap = _global_rate_limits.get(host.lower())
    if global_cap:
        caps.append(global_cap)
    try:
        rpm = int(node_rpm or 0)
        if rpm > 0:
            caps.append(rpm)
    except (TypeError, ValueError):
        pass
    return min(caps) if caps else None


async def _rate_limit_admit(host: str, rpm: int) -> float:
    """Block until the host's sliding one-minute window has a free slot, record
    the admission, and return the seconds actually waited."""
    waited = 0.0
    while True:
        async with _rate_lock:
            now = time.monotonic()
            hits = [t for t in _rate_hits.get(host, []) if now - t < 60.0]
            if len(hits) < rpm:
                hits.append(now)
                _rate_hits[host] = hits
                return waited
            delay = max(0.05, hits[0] + 60.0 - now)
            _rate_hits[host] = hits
        await asyncio.sleep(delay)
        waited += delay


async def _exec_http_request(params: dict) -> dict:
    """Generic HTTP call. Non-2xx raises by default so retry/onError apply;
    set ``allow_errors`` to get the response back regardless of status.
    Fase 6.6 — calls are throttled per host (node maxRequestsPerMinute and/or
    the global GRAPH_WORKFLOW_RATE_LIMITS map); throttled requests wait."""
    from urllib.parse import urlparse

    import httpx

    url = str(params.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("http.request: 'url' must be an http(s) URL")
    method = str(params.get("method") or "GET").upper()

    host = (urlparse(url).hostname or "").lower()
    rate_limited_s = 0.0
    rpm = _host_rate_limit(host, params.get("maxRequestsPerMinute")) if host else None
    if rpm:
        rate_limited_s = await _rate_limit_admit(host, rpm)

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

    out = {
        "status": resp.status_code,
        "ok": resp.is_success,
        "headers": dict(resp.headers),
        "json": parsed,
        "text": text,
    }
    if rate_limited_s > 0:
        out["rate_limited_s"] = round(rate_limited_s, 2)
    return out


def _validate_json_schema(value, schema: dict, path: str = "$") -> list[str]:
    """A dependency-free JSON Schema subset validator (fase 6.4): ``type``,
    ``required``, ``properties``, ``items`` and ``enum``. Returns a list of
    human-readable violations (empty = conforming)."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    expected = schema.get("type")
    if expected:
        py_types = {
            "object": dict, "array": list, "string": str,
            "boolean": bool, "null": type(None),
        }
        allowed = expected if isinstance(expected, list) else [expected]
        ok = False
        for t in allowed:
            if t == "number":
                ok = ok or (isinstance(value, (int, float)) and not isinstance(value, bool))
            elif t == "integer":
                ok = ok or (isinstance(value, int) and not isinstance(value, bool))
            elif t in py_types:
                ok = ok or isinstance(value, py_types[t])
        if not ok:
            errors.append(f"{path}: expected type {expected}, got {type(value).__name__}")
            return errors  # deeper checks are meaningless on the wrong type

    enum = schema.get("enum")
    if isinstance(enum, list) and enum and value not in enum:
        errors.append(f"{path}: value {value!r} is not one of {enum}")

    if isinstance(value, dict):
        for key in schema.get("required") or []:
            if key not in value:
                errors.append(f"{path}: missing required property '{key}'")
        props = schema.get("properties")
        if isinstance(props, dict):
            for key, sub in props.items():
                if key in value and isinstance(sub, dict):
                    errors.extend(_validate_json_schema(value[key], sub, f"{path}.{key}"))
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, it in enumerate(value):
                errors.extend(_validate_json_schema(it, items, f"{path}[{i}]"))
    return errors


async def _exec_subworkflow(
    db: aiosqlite.Connection, profile_id: str, params: dict, node_input, ctx: dict
) -> dict:
    """Run another workflow of the same profile inline as a child run and return
    its sink outputs. ``payload`` (or the node input) becomes the child's $trigger.
    Fase 6.4 — when the child declares contracts, the payload is validated against
    its ``input_schema`` before the run and the sink output against its
    ``output_schema`` on return."""
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

    if wf.input_schema:
        violations = _validate_json_schema(payload, wf.input_schema)
        if violations:
            raise ValueError(
                f"subworkflow: input does not match '{wf.name}' input contract: "
                + "; ".join(violations[:5])
            )

    graph_json = json.dumps(wf.graph.model_dump())
    child_run_id = await repo.create_run(db, wf_id, profile_id, "subworkflow", graph_json)
    # Inline (awaited) child execution: the parent node completes when the child
    # run does, and the child is observable like any other run (rows + SSE).
    await _execute(child_run_id, profile_id, wf.graph, "subworkflow", payload, depth=depth + 1)

    child = await repo.get_run(db, child_run_id)
    if child is None or child.status != "completed":
        raise RuntimeError(f"subworkflow: child run failed: {(child.error if child else None) or 'unknown error'}")

    node_outputs = ((await repo.get_run_context(db, child_run_id)) or {}).get("node", {})
    output = _sink_output(wf.graph, node_outputs)
    if wf.output_schema:
        violations = _validate_json_schema(output, wf.output_schema)
        if violations:
            raise RuntimeError(
                f"subworkflow: output does not match '{wf.name}' output contract: "
                + "; ".join(violations[:5])
            )
    return {"run_id": child_run_id, "workflow_id": wf_id, "status": child.status, "output": output}


async def run_workflow_sync(
    db: aiosqlite.Connection,
    workflow_id: str,
    profile_id: str,
    *,
    trigger_type: str = "manual",
    trigger_payload: dict | None = None,
    depth: int = 0,
    dry_run: bool = False,
    use_pins: bool = False,
) -> dict:
    """Phase 41 (fase 9.1/9.2/9.3) — run a workflow inline (awaited) and return
    its result, the way ``_exec_subworkflow`` does but for external callers
    (the tool bridge, the MCP server, the chat endpoint). ``trigger_payload``
    becomes ``$trigger``. Returns {run_id, status, output, error, reply}, where
    ``reply`` is the ``chat.reply`` node's text when the graph has one.

    The run is a first-class row (observable via the Runs view + SSE) so stats
    (fase 5.1) and audit apply. ``depth`` is forwarded to the subworkflow depth
    guard so a nested composition still can't exceed the max nesting.

    Fase 11.1/11.2 — ``use_pins`` (test suites) and ``dry_run`` (full dry-run)
    mock external-effect nodes; see ``_mock_dispatch``. Output-contract
    validation is skipped for a dry-run since a placeholder output need not
    satisfy it."""
    wf = await repo.get_workflow(db, workflow_id)
    if wf is None or wf.profile_id != profile_id:
        raise ValueError("workflow not found")

    payload = trigger_payload if isinstance(trigger_payload, dict) else {}
    if wf.input_schema and not dry_run:
        violations = _validate_json_schema(payload, wf.input_schema)
        if violations:
            raise ValueError(
                f"input does not match '{wf.name}' input contract: "
                + "; ".join(violations[:5])
            )

    graph_json = json.dumps(wf.graph.model_dump())
    run_id = await repo.create_run(db, workflow_id, profile_id, trigger_type, graph_json)
    await _execute(
        run_id, profile_id, wf.graph, trigger_type, payload, depth=depth,
        dry_run=dry_run, use_pins=use_pins,
    )

    run = await repo.get_run(db, run_id)
    node_outputs = ((await repo.get_run_context(db, run_id)) or {}).get("node", {})
    output = _sink_output(wf.graph, node_outputs)
    reply = _extract_chat_reply(wf.graph, node_outputs)
    status = run.status if run else "failed"
    if status == "completed" and wf.output_schema and not dry_run:
        violations = _validate_json_schema(output, wf.output_schema)
        if violations:
            raise RuntimeError(
                f"output does not match '{wf.name}' output contract: "
                + "; ".join(violations[:5])
            )
    return {
        "run_id": run_id,
        "status": status,
        "output": output,
        "error": (run.error if run else None),
        "reply": reply,
    }


def _extract_chat_reply(graph: WorkflowGraph, node_ctx: dict) -> str | None:
    """The text emitted by the graph's ``chat.reply`` node(s) (fase 9.3), or None
    when the graph has none. Concatenates when there is more than one."""
    replies: list[str] = []
    for n in graph.nodes:
        if n.type != "chat.reply":
            continue
        out = node_ctx.get(n.id, {}).get("output")
        if isinstance(out, dict) and isinstance(out.get("reply"), str):
            replies.append(out["reply"])
    if not replies:
        return None
    return "\n\n".join(replies)


# ── fase 11.2: full dry-run ──────────────────────────────────────────────────

async def dry_run_workflow(
    db: aiosqlite.Connection, workflow_id: str, profile_id: str, payload: dict | None = None,
) -> dict:
    """Fase 11.2 — simulate the whole graph without external side effects:
    ``http.request``, ``db.query``, ``notification.*``/``email.*`` and
    ``llm.*`` are mocked (a pin when the node has one, else a typed
    placeholder — see ``_mock_dispatch``). Returns a report: the execution
    path, every node's simulated output and the external effects a real run
    would have performed. To be used before activating a schedule on a new
    graph."""
    wf = await repo.get_workflow(db, workflow_id)
    if wf is None or wf.profile_id != profile_id:
        raise ValueError("workflow not found")

    graph_json = json.dumps(wf.graph.model_dump())
    run_id = await repo.create_run(db, workflow_id, profile_id, "manual", graph_json)
    await _execute(run_id, profile_id, wf.graph, "manual", payload or {}, dry_run=True)

    run = await repo.get_run(db, run_id)
    ctx_persisted = (await repo.get_run_context(db, run_id)) or {}
    node_ctx = ctx_persisted.get("node") or {}
    node_runs = await repo.list_node_runs(db, run_id)
    path = [nr.node_id for nr in node_runs] if node_runs else list(node_ctx.keys())
    return {
        "run_id": run_id,
        "status": run.status if run else "failed",
        "path": path,
        "node_outputs": {nid: entry.get("output") for nid, entry in node_ctx.items()},
        "external_effects": ctx_persisted.get("dry_effects") or [],
        "error": run.error if run else None,
    }


# ── fase 11.1: workflow test suites ─────────────────────────────────────────

def _json_path_get(value, path: str):
    cur = value
    for part in path.split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def _evaluate_assertion(assertion, actual, node_ran: bool) -> tuple[bool, str]:
    if not node_ran:
        return False, f"node '{assertion.node_id}' did not run"
    if assertion.type == "equals":
        ok = actual == assertion.expected
        return ok, "" if ok else f"expected {assertion.expected!r}, got {actual!r}"
    if assertion.type == "contains":
        try:
            if isinstance(actual, str):
                ok = str(assertion.expected) in actual
            elif isinstance(actual, dict):
                ok = assertion.expected in actual
            elif isinstance(actual, (list, tuple)):
                ok = assertion.expected in actual
            else:
                ok = False
        except Exception:  # noqa: BLE001 — an unhashable/uncomparable expected value just fails the check
            ok = False
        return ok, "" if ok else f"{actual!r} does not contain {assertion.expected!r}"
    if assertion.type == "json_path":
        value = _json_path_get(actual, assertion.path or "")
        ok = value == assertion.expected
        return ok, "" if ok else f"path '{assertion.path}' = {value!r}, expected {assertion.expected!r}"
    if assertion.type == "schema":
        schema = assertion.expected if isinstance(assertion.expected, dict) else {}
        violations = _validate_json_schema(actual, schema)
        return (not violations), "; ".join(violations[:3])
    return False, f"unknown assertion type '{assertion.type}'"


async def run_test_case(db: aiosqlite.Connection, workflow_id: str, profile_id: str, case) -> dict:
    """Fase 11.1 — run one saved test case: executes the workflow with the
    case's fixture ``$trigger``, letting any pinned node (fase 3.2) replace an
    external call for a deterministic result, then evaluates each assertion
    against the actual node output. Never raises — a setup failure (e.g. the
    workflow was deleted) surfaces as a failed result, not an exception."""
    from app.schemas.graph_workflows import TestAssertionResultOut, TestCaseResultOut

    try:
        wf = await repo.get_workflow(db, workflow_id)
        if wf is None or wf.profile_id != profile_id:
            raise ValueError("workflow not found")
        graph_json = json.dumps(wf.graph.model_dump())
        run_id = await repo.create_run(db, workflow_id, profile_id, "manual", graph_json)
        await _execute(run_id, profile_id, wf.graph, "manual", case.trigger_payload or {}, use_pins=True)

        run = await repo.get_run(db, run_id)
        node_ctx = ((await repo.get_run_context(db, run_id)) or {}).get("node") or {}
        run_ok = run is not None and run.status == "completed"

        results = []
        all_passed = run_ok
        for a in case.assertions:
            entry = node_ctx.get(a.node_id)
            actual = entry.get("output") if entry else None
            passed, message = _evaluate_assertion(a, actual, node_ran=entry is not None)
            all_passed = all_passed and passed
            results.append(TestAssertionResultOut(
                node_id=a.node_id, type=a.type, expected=a.expected,
                actual=_jsonable(actual), passed=passed, message=message,
            ))
        return TestCaseResultOut(
            case_id=case.id, name=case.name, passed=all_passed, run_id=run_id,
            error=None if run_ok else ((run.error if run else None) or f"run ended in status '{run.status if run else 'unknown'}'"),
            assertions=results,
        )
    except Exception as exc:  # noqa: BLE001 — surface as a failed case, not a 500
        return TestCaseResultOut(case_id=case.id, name=case.name, passed=False, run_id=None, error=str(exc), assertions=[])


async def run_test_suite(db: aiosqlite.Connection, workflow_id: str, profile_id: str) -> dict:
    """Fase 11.1 — run every saved test case ("Run tests" in the toolbar)."""
    from app.schemas.graph_workflows import TestSuiteRunOut

    cases = await repo.list_test_cases(db, workflow_id)
    results = [await run_test_case(db, workflow_id, profile_id, c) for c in cases]
    passed = sum(1 for r in results if r.passed)
    return TestSuiteRunOut(
        workflow_id=workflow_id, total=len(results), passed=passed,
        failed=len(results) - passed, results=results,
    )


# ── fase 11.3: pre-run cost estimate ────────────────────────────────────────

_LLM_NODE_TYPES = frozenset({"llm.completion", "llm.agent", "llm.classify", "llm.extract", "llm.judge"})


def _estimate_runs_per_month(triggers) -> float | None:
    """Fase 11.3 — projected runs/month from the workflow's *enabled* schedule
    trigger(s): the gap between two consecutive fires of each recurrence,
    extrapolated to a 30-day month. None when there is no active schedule."""
    from zoneinfo import ZoneInfo

    from app.services import reminder_parsing

    schedules = [t for t in triggers if t.type == "schedule" and t.enabled]
    if not schedules:
        return None
    tz = ZoneInfo(settings.timezone) if getattr(settings, "timezone", None) else ZoneInfo("UTC")
    now = int(time.time())
    total = 0.0
    for t in schedules:
        recurrence = (t.config or {}).get("recurrence", "once")
        first = reminder_parsing.compute_next_fire(recurrence, now, tz)
        if first is None:
            continue
        second = reminder_parsing.compute_next_fire(recurrence, first, tz)
        if second is None or second <= first:
            continue
        total += (30 * 86400) / (second - first)
    return total if total > 0 else None


async def cost_estimate(db: aiosqlite.Connection, workflow_id: str, profile_id: str) -> dict:
    """Fase 11.3 — a static token/month projection: (LLM node count and)
    historical average tokens per run (fase 5.1/7.4 stats) × the workflow's
    active schedule frequency. Tokens only, no invented price list — the
    editor/stats dashboard decides how to present the number."""
    from app.schemas.graph_workflows import WorkflowCostEstimateOut

    wf = await repo.get_workflow(db, workflow_id)
    if wf is None or wf.profile_id != profile_id:
        raise ValueError("workflow not found")

    llm_node_ids = {n.id for n in wf.graph.nodes if n.type in _LLM_NODE_TYPES}

    avg_tokens_per_run: float | None = None
    basis = "no LLM nodes in this graph"
    if llm_node_ids:
        node_stats = await repo.node_stats_for_workflow(db, workflow_id)
        total_tokens = sum(s.tokens_total for s in node_stats if s.node_id in llm_node_ids)
        wf_stats = await repo.workflow_stats_for_profile(db, profile_id)
        wf_stat = next((s for s in wf_stats if s.workflow_id == workflow_id), None)
        runs = wf_stat.runs if wf_stat else 0
        if runs > 0 and total_tokens > 0:
            avg_tokens_per_run = total_tokens / runs
            basis = f"historical average over {runs} run(s)"
        else:
            basis = "no run history yet for this workflow's LLM node(s)"

    triggers = await repo.list_triggers(db, workflow_id)
    runs_per_month = _estimate_runs_per_month(triggers)
    tokens_per_month = (
        avg_tokens_per_run * runs_per_month
        if avg_tokens_per_run is not None and runs_per_month is not None
        else None
    )
    if runs_per_month is None:
        basis += "; no active schedule trigger, so no monthly projection"

    return WorkflowCostEstimateOut(
        workflow_id=workflow_id,
        llm_node_count=len(llm_node_ids),
        avg_tokens_per_run=avg_tokens_per_run,
        runs_per_month_est=runs_per_month,
        tokens_per_month_est=tokens_per_month,
        basis=basis,
    )


async def budget_status(db: aiosqlite.Connection, workflow_id: str, profile_id: str) -> dict:
    """Fase 12.1 — GET /{id}/budget: this workflow's own caps and usage for the
    current period, plus the profile-wide ("workspace") cap/usage it is also
    gated by (whichever of the two is tighter hard-stops new runs first)."""
    wf = await repo.get_workflow(db, workflow_id)
    if wf is None or wf.profile_id != profile_id:
        raise ValueError("workflow not found")

    period, period_start = _month_period()
    usage = await repo.workflow_usage_for_period(db, workflow_id, period_start)
    exceeded = (
        (wf.token_budget_month is not None and usage["tokens_total"] >= wf.token_budget_month) or
        (wf.run_budget_month is not None and usage["runs"] >= wf.run_budget_month)
    )

    profile_budget = await repo.get_profile_budget(db, profile_id)
    profile_usage = await repo.profile_usage_for_period(db, profile_id, period_start)
    profile_token_budget = profile_budget["token_budget_month"] if profile_budget else None
    profile_run_budget = profile_budget["run_budget_month"] if profile_budget else None
    profile_exceeded = (
        (profile_token_budget is not None and profile_usage["tokens_total"] >= profile_token_budget) or
        (profile_run_budget is not None and profile_usage["runs"] >= profile_run_budget)
    )

    return {
        "workflow_id": workflow_id,
        "period": period,
        "token_budget_month": wf.token_budget_month,
        "run_budget_month": wf.run_budget_month,
        "tokens_used": usage["tokens_total"],
        "runs_used": usage["runs"],
        "exceeded": exceeded,
        "profile_token_budget_month": profile_token_budget,
        "profile_run_budget_month": profile_run_budget,
        "profile_tokens_used": profile_usage["tokens_total"],
        "profile_runs_used": profile_usage["runs"],
        "profile_exceeded": profile_exceeded,
    }


async def _exec_kb_search(
    db: aiosqlite.Connection, profile_id: str, params: dict, node_input
) -> dict:
    """Fase 6.5 — semantic search over the profile's knowledge base (Phase 28),
    returning structured hits instead of the tool's flattened text: RAG inside
    workflows without going through a generic llm.agent."""
    from app.services import rag_service

    query = params.get("query")
    if query in (None, ""):
        query = node_input
    if query in (None, ""):
        raise ValueError("kb.search: 'query' is required")
    if not isinstance(query, str):
        query = json.dumps(query, default=str, ensure_ascii=False)
    query = query.strip()
    if not query:
        raise ValueError("kb.search: 'query' is required")
    top_k = max(1, min(int(params.get("top_k") or 5), 20))
    document_ids = params.get("document_ids")
    if isinstance(document_ids, str):
        document_ids = [d.strip() for d in document_ids.split(",") if d.strip()]
    if not isinstance(document_ids, list) or not document_ids:
        document_ids = None
    sources = await rag_service.retrieve(
        db, profile_id, query, top_k=top_k, document_ids=document_ids
    )
    results = [
        {
            "text": s.snippet,
            "score": round(float(s.score), 4),
            "source": s.filename,
            "chunk_index": s.chunk_index,
        }
        for s in sources
    ]
    return {"results": results, "count": len(results), "query": query}


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


# ── Phase 52 (roadmap fase 20) — Telegram as a workflow channel ───────────────

def _telegram_bot():
    """The live bot Application, or None when the bot isn't running. Kept behind a
    helper so every telegram.* node degrades to a clean no-op off Telegram."""
    from app.telegram import bot as telegram_bot

    app = telegram_bot.get_bot()
    return app.bot if app is not None else None


def _resolve_chat_id(params: dict, ctx: dict):
    """A telegram.* node targets an explicit ``chat_id`` (expression, already
    resolved) or, failing that, the chat the run originated from (a ``telegram`` /
    ``chat`` trigger puts ``chat_id`` on ``$trigger``)."""
    chat_id = params.get("chat_id")
    if chat_id in (None, ""):
        trigger = ctx.get("trigger") if isinstance(ctx.get("trigger"), dict) else {}
        chat_id = trigger.get("chat_id")
    if chat_id in (None, ""):
        raise ValueError("telegram: no 'chat_id' given and the run has no originating chat")
    return chat_id


def _telegram_parse_mode(params: dict) -> str | None:
    parse_mode = str(params.get("parse_mode") or "").strip()
    if parse_mode not in _TELEGRAM_PARSE_MODES:
        raise ValueError(f"telegram: invalid parse_mode {parse_mode!r} (Markdown|MarkdownV2|HTML)")
    return parse_mode or None


async def _exec_telegram_send(params: dict, node_input, ctx: dict) -> dict:
    """20.2 — send text to any chat/thread. Off Telegram it no-ops (``sent:
    False``), mirroring the silent-drop of the notify bridge; a send that raises
    (e.g. a chat the bot doesn't own) surfaces so On error applies."""
    chat_id = _resolve_chat_id(params, ctx)
    text = params.get("text")
    if text in (None, ""):
        text = node_input
    if not isinstance(text, str):
        text = json.dumps(text, default=str, ensure_ascii=False)
    bot = _telegram_bot()
    if bot is None:
        return {"sent": False, "reason": "bot_not_running", "chat_id": chat_id}
    kwargs: dict = {"chat_id": chat_id, "text": text, "parse_mode": _telegram_parse_mode(params)}
    if params.get("thread_id") not in (None, ""):
        kwargs["message_thread_id"] = int(params["thread_id"])
    if params.get("reply_to") not in (None, ""):
        kwargs["reply_to_message_id"] = int(params["reply_to"])
    if _as_bool(params.get("disable_preview")):
        kwargs["disable_web_page_preview"] = True
    try:
        msg = await bot.send_message(**kwargs)
    except Exception as exc:  # noqa: BLE001 — surface as a node failure (retry/onError)
        raise RuntimeError(f"telegram.send: {exc}") from exc
    return {"sent": True, "message_id": msg.message_id, "chat_id": msg.chat_id}


async def _exec_telegram_send_media(params: dict, ctx: dict) -> dict:
    """20.2 — send a photo/document from workspace storage or a URL, with caption."""
    chat_id = _resolve_chat_id(params, ctx)
    kind = str(params.get("media_type") or "document").strip().lower()
    source = params.get("url") or params.get("path")
    if not source:
        raise ValueError("telegram.sendMedia: 'url' or 'path' is required")
    caption = params.get("caption")
    bot = _telegram_bot()
    if bot is None:
        return {"sent": False, "reason": "bot_not_running", "chat_id": chat_id}
    # A path is confined to the workspace storage (fase 4.2); a URL is passed through.
    media = str(source)
    fh = None
    if not media.startswith(("http://", "https://")):
        fh = open(_safe_workspace_path(media), "rb")  # noqa: SIM115 — closed below
        media = fh
    senders = {
        "photo": ("send_photo", "photo"), "document": ("send_document", "document"),
        "audio": ("send_audio", "audio"), "voice": ("send_voice", "voice"),
        "video": ("send_video", "video"),
    }
    method_name, arg = senders.get(kind, senders["document"])
    try:
        msg = await getattr(bot, method_name)(chat_id=chat_id, caption=caption, **{arg: media})
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"telegram.sendMedia: {exc}") from exc
    finally:
        if fh is not None:
            fh.close()
    return {"sent": True, "message_id": msg.message_id, "chat_id": msg.chat_id, "media_type": kind}


async def _exec_telegram_edit(params: dict, ctx: dict) -> dict:
    """20.2 — edit a message sent earlier in the run (progress bars, done edits)."""
    chat_id = _resolve_chat_id(params, ctx)
    message_id = params.get("message_id")
    if message_id in (None, ""):
        raise ValueError("telegram.editMessage: 'message_id' is required")
    text = params.get("text")
    if text in (None, ""):
        raise ValueError("telegram.editMessage: 'text' is required")
    bot = _telegram_bot()
    if bot is None:
        return {"edited": False, "reason": "bot_not_running", "chat_id": chat_id}
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=int(message_id), text=str(text),
            parse_mode=_telegram_parse_mode(params),
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"telegram.editMessage: {exc}") from exc
    return {"edited": True, "message_id": int(message_id), "chat_id": chat_id}


async def _exec_telegram_delete(params: dict, ctx: dict) -> dict:
    """20.2 — remove a message sent earlier in the run."""
    chat_id = _resolve_chat_id(params, ctx)
    message_id = params.get("message_id")
    if message_id in (None, ""):
        raise ValueError("telegram.deleteMessage: 'message_id' is required")
    bot = _telegram_bot()
    if bot is None:
        return {"deleted": False, "reason": "bot_not_running", "chat_id": chat_id}
    try:
        await bot.delete_message(chat_id=chat_id, message_id=int(message_id))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"telegram.deleteMessage: {exc}") from exc
    return {"deleted": True, "message_id": int(message_id), "chat_id": chat_id}


async def _exec_telegram_ask(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, params: dict, ctx: dict
) -> tuple[object, list[str]]:
    """20.3 — present inline buttons on Telegram, suspend the run (reusing the
    ``wait.event`` correlation machinery), and resume with the tapped
    ``callback_data`` as output. ``onTimeout`` (branch|fail) governs a timeout."""
    run_id = ctx.get("_run_id")
    if not run_id:
        raise ValueError("telegram.ask: only runs inside a real execution")
    chat_id = _resolve_chat_id(params, ctx)
    question = str(params.get("text") or params.get("question") or "").strip()
    options = params.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError("telegram.ask: 'options' must be a non-empty list of {label, value}")

    run = await repo.get_run(db, run_id)
    workflow_id = run.workflow_id if run else ""
    approval = await repo.get_pending_approval(db, run_id, node.id)
    if approval is None:
        correlation_id = f"tgask:{run_id}:{node.id}"
        timeout_s = float(params.get("timeout") or 3600)
        timeout_s = max(1.0, min(timeout_s, float(settings.graph_workflow_approval_max_timeout)))
        approval = await repo.create_approval(
            db, run_id, node.id, workflow_id, profile_id,
            title=question or "?", message="", timeout_at=int(time.time() + timeout_s),
            kind="event", correlation_id=correlation_id,
        )
        # Send the buttons; each callback_data carries the correlation id + value.
        bot = _telegram_bot()
        if bot is not None:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            rows = []
            for opt in options:
                label = str((opt or {}).get("label") or (opt or {}).get("value") or "")
                value = str((opt or {}).get("value") or label)
                rows.append([InlineKeyboardButton(label, callback_data=f"wfask:{approval.id}:{value}")])
            try:
                await bot.send_message(
                    chat_id=chat_id, text=question or "?",
                    reply_markup=InlineKeyboardMarkup(rows),
                )
            except Exception:  # noqa: BLE001 — a failed prompt still waits (deliverable via API)
                logger.warning("telegram.ask: failed to send prompt to chat %s", chat_id)

    current = await _wait_for_decision(db, run_id, approval)
    if current.status == "delivered":
        payload = current.data if isinstance(current.data, dict) else {"value": current.data}
        return payload, ["main"]
    if current.status == "expired" and str(params.get("onTimeout") or "branch").lower() == "fail":
        raise RuntimeError("telegram.ask: timed out waiting for a response")
    return {}, ["timeout"]


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
    # Fase 9.1 — the profile's workflows published as tools (active + input
    # contract + expose_as_tool). Namespaced ``workflow__<id>`` so execute_tool
    # routes them to a nested workflow run.
    try:
        from app.services import workflow_tool_service

        tools.extend(await workflow_tool_service.get_tool_definitions(db, profile_id))
    except Exception:  # noqa: BLE001
        logger.exception("graph llm.agent: workflow tool listing failed; continuing without them")
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


# ── structured LLM nodes (Phase 35 — roadmap fase 4.1) ─────────────────────

def _parse_llm_json(content: str) -> object:
    """The JSON value inside an LLM reply: tolerates code fences and prose
    around the first JSON object/array. Raises ``ValueError`` when none parses
    (so node retry/onError apply)."""
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError(f"no JSON found in the model reply: {text[:200]!r}")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"model reply is not valid JSON: {exc}") from None
    return value


async def _llm_json_call(
    db: aiosqlite.Connection, profile_id: str, params: dict, system: str, prompt: str
) -> tuple[object, dict]:
    """One completion (with failover chain + response cache, like llm.completion)
    that MUST come back as JSON. Returns (parsed_value, meta)."""
    from app.schemas.chat import ChatCompletionRequest, ChatMessage

    model = params.get("model") or settings.default_model
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=prompt),
    ]
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
            choices = response.get("choices") or []
            content = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
            value = _parse_llm_json(content)
        except Exception as exc:  # noqa: BLE001 — a bad reply (call failure or invalid JSON)
            # falls through to the next chain candidate, same as a provider failure
            last_exc = exc
            continue
        meta = {"model": candidate, "_usage": _extract_usage(response), "_cache": cache_status}
        if len(candidates) > 1:
            meta["_failover"] = {"tried": tried, "used": candidate}
        return value, meta
    raise last_exc


def _classify_categories(params: dict) -> list[str]:
    raw = params.get("categories")
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = None
        else:
            raw = [c.strip() for c in text.split(",") if c.strip()]
    if not isinstance(raw, list) or not raw:
        raise ValueError("llm.classify: 'categories' must be a non-empty array (or comma-separated list)")
    return [str(c) for c in raw]


async def _exec_llm_classify(
    db: aiosqlite.Connection, profile_id: str, params: dict, node_input
) -> dict:
    """Guaranteed-structured classification: the model must answer with a JSON
    object whose ``category`` is one of the allowed values — anything else
    raises, so retry/onError apply instead of garbage flowing downstream."""
    categories = _classify_categories(params)
    text = params.get("input") or params.get("text")
    if text is None or str(text) == "":
        text = node_input
    if not isinstance(text, str):
        text = json.dumps(text, default=str, ensure_ascii=False)
    instructions = str(params.get("instructions") or "").strip()
    system = (
        "You are a strict classifier. Reply with ONLY a JSON object — no prose, no code fences — "
        'shaped exactly like {"category": "<one allowed category>", "confidence": <number 0..1>}. '
        f"Allowed categories: {json.dumps(categories, ensure_ascii=False)}."
        + (f" Additional instructions: {instructions}" if instructions else "")
    )
    data, meta = await _llm_json_call(db, profile_id, params, system, text)
    if not isinstance(data, dict):
        raise ValueError("llm.classify: model did not return a JSON object")
    category = str(data.get("category") or "")
    if category not in categories:
        # Tolerate case slips before failing — determinism beats strictness here.
        by_lower = {c.lower(): c for c in categories}
        if category.lower() in by_lower:
            category = by_lower[category.lower()]
        else:
            raise ValueError(f"llm.classify: model returned {category!r}, not one of {categories}")
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = None
    return {"category": category, "confidence": confidence, **meta}


async def _exec_llm_extract(
    db: aiosqlite.Connection, profile_id: str, params: dict, node_input
) -> dict:
    """Guaranteed-structured extraction against a JSON Schema declared in the
    inspector. Top-level ``required`` properties are enforced; a non-conforming
    reply raises, so retry/onError apply."""
    schema = params.get("schema")
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except json.JSONDecodeError as exc:
            raise ValueError(f"llm.extract: 'schema' is not valid JSON: {exc}") from None
    if not isinstance(schema, dict) or not schema:
        raise ValueError("llm.extract: 'schema' must be a JSON Schema object")
    text = params.get("input") or params.get("text")
    if text is None or str(text) == "":
        text = node_input
    if not isinstance(text, str):
        text = json.dumps(text, default=str, ensure_ascii=False)
    instructions = str(params.get("instructions") or "").strip()
    system = (
        "You extract structured data. Reply with ONLY a JSON value matching this JSON Schema "
        "— no prose, no code fences, no extra keys: "
        f"{json.dumps(schema, ensure_ascii=False)}."
        + (f" Additional instructions: {instructions}" if instructions else "")
    )
    data, meta = await _llm_json_call(db, profile_id, params, system, text)
    required = schema.get("required")
    if isinstance(required, list) and isinstance(data, dict):
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(f"llm.extract: model reply is missing required properties: {missing}")
    elif isinstance(required, list) and not isinstance(data, dict):
        raise ValueError("llm.extract: model did not return a JSON object")
    return {"data": data, **meta}


# ── Phase 50 (roadmap fase 18 — LLM quality) ────────────────────────────────

def _judge_scale_max(params: dict) -> int:
    raw = params.get("scaleMax")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = settings.graph_workflow_judge_default_scale_max
    return value if value >= 2 else 2


async def _exec_llm_judge(
    db: aiosqlite.Connection, profile_id: str, params: dict, node_input
) -> tuple[dict, list[str]]:
    """Fase 18.1 — evaluate content against a rubric on a 1..scaleMax scale and
    route to the ``pass``/``fail`` handle by a threshold. The score/threshold
    decides ``passed`` (authoritative), so a generate → judge → regenerate loop
    (`while`) or a quality gate before publishing keeps a deterministic gate even
    when the model's own ``verdict`` disagrees. Shares the model picker, failover
    chain and response cache with the other ``llm.*`` nodes; the judge model can
    differ from the generator's."""
    scale_max = _judge_scale_max(params)
    threshold = params.get("threshold")
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        # Default gate: at least 60% of the scale (rounded up), so a 1..5 scale
        # passes from 3 and a 1..10 scale from 6 without extra configuration.
        threshold = float(math.ceil(scale_max * 0.6))

    criteria = str(params.get("criteria") or "").strip()
    if not criteria:
        raise ValueError("llm.judge: 'criteria' (the rubric to score against) is required")

    content = params.get("input") or params.get("text")
    if content is None or str(content) == "":
        content = node_input
    if not isinstance(content, str):
        content = json.dumps(content, default=str, ensure_ascii=False)

    reference = params.get("reference")
    if reference is not None and not isinstance(reference, str):
        reference = json.dumps(reference, default=str, ensure_ascii=False)
    instructions = str(params.get("instructions") or "").strip()

    system = (
        "You are a strict, impartial evaluator. Score the CONTENT against the CRITERIA on an "
        f"integer scale from 1 to {scale_max} (higher is better). Reply with ONLY a JSON object — "
        'no prose, no code fences — shaped exactly like '
        '{"score": <integer>, "verdict": "pass"|"fail", "rationale": "<one short sentence>"}.'
        + (f" Additional instructions: {instructions}" if instructions else "")
    )
    prompt = f"CRITERIA:\n{criteria}\n\nCONTENT:\n{content}"
    if reference:
        prompt += f"\n\nREFERENCE (the ideal answer to compare against):\n{reference}"

    data, meta = await _llm_json_call(db, profile_id, params, system, prompt)
    if not isinstance(data, dict):
        raise ValueError("llm.judge: model did not return a JSON object")
    raw_score = data.get("score")
    if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
        raise ValueError("llm.judge: model reply is missing a numeric 'score'")
    score = max(1, min(scale_max, int(round(raw_score))))
    passed = score >= threshold
    rationale = data.get("rationale")
    if not isinstance(rationale, str):
        rationale = None
    result = {
        "score": score,
        "scaleMax": scale_max,
        "threshold": threshold,
        "passed": passed,
        "verdict": "pass" if passed else "fail",
        "rationale": rationale,
        **meta,
    }
    return result, ["pass" if passed else "fail"]


# ── Phase 50 (roadmap fase 18.2 — prompt A/B testing) ───────────────────────

_VARIANT_STATE_PREFIX = "__abtest__"


def _variant_list(node_params: dict) -> list[dict]:
    """The declared A/B variants of a node, each ``{name?, weight?, params: {}}``.
    Tolerates a JSON string (raw inspector field) and drops malformed entries."""
    raw = node_params.get("variants")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [v for v in raw if isinstance(v, dict) and isinstance(v.get("params"), dict)]


async def _select_variant(
    db: aiosqlite.Connection, workflow_id: str | None, node: GraphNode,
) -> tuple[str, dict] | None:
    """Fase 18.2 — pick one prompt/model variant for a node and return
    ``(name, merged_raw_params)``; None when the node declares no variants.

    ``round-robin`` (default) alternates evenly across runs via a per-node
    counter persisted in ``workflow_state`` (survives restarts); ``weighted``
    samples by each variant's ``weight``. The chosen variant's ``params`` overlay
    the node's own params, and the A/B keys are stripped so selection never
    re-enters. Without a workflow context (single-node test / remote dispatch)
    round-robin degrades to a stateless random pick."""
    variants = _variant_list(node.params)
    if not variants:
        return None
    strategy = str(node.params.get("variantStrategy") or "round-robin")
    if strategy == "weighted":
        weights = [max(0.0, float(v.get("weight", 1) or 0)) for v in variants]
        if sum(weights) <= 0:
            weights = [1.0] * len(variants)
        idx = random.choices(range(len(variants)), weights=weights, k=1)[0]
    elif workflow_id:
        count = await repo.state_increment(db, workflow_id, _VARIANT_STATE_PREFIX + node.id, 1, None)
        idx = (int(count) - 1) % len(variants)
    else:
        idx = random.randrange(len(variants))
    chosen = variants[idx]
    name = str(chosen.get("name") or f"variant-{idx + 1}")
    merged = {**node.params, **chosen["params"]}
    merged.pop("variants", None)
    merged.pop("variantStrategy", None)
    return name, merged


# ── export/import & generation helpers (Phase 36 — roadmap fase 5) ─────────

_SECRET_REF_RE = re.compile(r"\$secrets\.([A-Za-z_][A-Za-z0-9_]*)")


def secret_references(graph: WorkflowGraph) -> list[str]:
    """The distinct `$secrets.<name>` references used anywhere in the graph —
    exported alongside the definition (fase 5.2) so an import can tell which
    secrets must be re-created in the target environment (values never travel)."""
    found = _SECRET_REF_RE.findall(json.dumps(graph.model_dump()))
    return sorted(set(found))


def redact_graph_for_export(graph: WorkflowGraph) -> dict:
    """Fase 12.2 — a graph dict fit to leave the system (export, share): each
    node's fase-3.2 ``pinnedOutput`` (a frozen real output, e.g. a captured
    webhook payload) goes through its own ``redact`` paths exactly like a live
    run's persisted output does, since it is otherwise the one place actual
    production data rides along with the portable definition."""
    data = graph.model_dump()
    for node in data.get("nodes", []):
        if node.get("pinnedOutput") is not None and node.get("redact"):
            node["pinnedOutput"] = _redact_output(node["pinnedOutput"], node["redact"])
    return data


async def validate_import(
    db: aiosqlite.Connection, profile_id: str, graph: WorkflowGraph
) -> list[str]:
    """Non-blocking import validation (fase 5.2): unknown node types (a renamed
    tool, an MCP server not configured here) and `$secrets` references missing
    from this profile become warnings, not errors — the workflow still imports
    and can be fixed in the editor."""
    from app.data.node_catalog import node_catalog

    warnings: list[str] = []
    if len(graph.nodes) > settings.graph_workflow_max_nodes:
        raise ValueError(f"graph exceeds the {settings.graph_workflow_max_nodes}-node limit")
    known = {t.type for t in await node_catalog(db, profile_id)}
    for n in graph.nodes:
        if n.type not in known:
            warnings.append(f"unknown node type '{n.type}' (node '{n.id}') — not available in this environment")
    ids = {n.id for n in graph.nodes}
    for e in graph.edges:
        if e.source not in ids or e.target not in ids:
            warnings.append(f"edge '{e.id}' references a missing node")
    stored = set(await repo.get_encrypted_secrets(db, profile_id))
    for name in secret_references(graph):
        if name not in stored:
            warnings.append(f"$secrets.{name} is referenced but not defined in this profile")
    return warnings


def _generation_catalog_context(catalog) -> str:
    """A compact, token-cheap description of every node type the generator may
    use: `type [outputs] (params)` one per line."""
    lines = []
    for t in catalog:
        params = ", ".join(p.get("name") for p in t.params_schema if p.get("name"))
        outputs = "/".join(t.outputs)
        lines.append(f"- {t.type} [{outputs}]" + (f" params: {params}" if params else ""))
    return "\n".join(lines)


def _layout_generated_nodes(graph: WorkflowGraph) -> None:
    """Give nodes without a position a simple layered (longest-path) layout so
    the draft opens readable on the canvas."""
    if all(isinstance(n.position, dict) and "x" in n.position for n in graph.nodes):
        return
    incoming: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        if e.target in incoming and e.source in incoming:
            incoming[e.target].append(e.source)
    level: dict[str, int] = {}

    def depth(nid: str, seen: frozenset = frozenset()) -> int:
        if nid in level:
            return level[nid]
        if nid in seen:  # cycle guard — generated graphs may be malformed
            return 0
        d = 0 if not incoming[nid] else 1 + max(depth(p, seen | {nid}) for p in incoming[nid])
        level[nid] = d
        return d

    per_level: dict[int, int] = {}
    for n in graph.nodes:
        d = depth(n.id)
        row = per_level.get(d, 0)
        per_level[d] = row + 1
        n.position = {"x": 40 + d * 260, "y": 60 + row * 130}


async def generate_workflow(
    db: aiosqlite.Connection,
    profile_id: str,
    prompt: str,
    model: str | None = None,
    failover_chain: str | None = None,
    on_progress=None,
) -> dict:
    """Fase 5.3 — "describe what you want" → a validated draft graph. The node
    catalog (types, outputs, param names) is the model's context; the reply is
    parsed, unknown node types and broken edges are dropped with warnings, a
    missing trigger gets a `manual` node prepended, and positions are laid out.
    The draft is returned, NOT saved — the editor opens it for review.

    ``failover_chain`` names a Settings → Models chain to fall back through on
    call failure (same semantics as llm.* nodes). ``on_progress(step, detail)``
    — when given — is invoked at each stage so the UI can show a live log
    (steps: catalog, calling, received, normalized, trigger_added, layout)."""
    from app.data.node_catalog import node_catalog

    def progress(step: str, detail: dict | None = None) -> None:
        if on_progress is not None:
            try:
                on_progress(step, detail or {})
            except Exception:  # noqa: BLE001 — a UI log must never break generation
                logger.exception("generate_workflow: on_progress callback failed")

    catalog = await node_catalog(db, profile_id)
    known = {t.type for t in catalog}
    progress("catalog", {"count": len(catalog)})
    system = (
        "You design workflow graphs for a visual DAG engine. Reply with ONLY a JSON object "
        "— no prose, no code fences — shaped exactly like: "
        '{"name": "<short title>", "description": "<one sentence>", "graph": {"nodes": '
        '[{"id": "<slug>", "type": "<node type>", "name": "<label>", "params": {…}}], '
        '"edges": [{"id": "e1", "source": "<node id>", "target": "<node id>", '
        '"sourceHandle": "<optional: true/false for if, case:<v>/default for switch, '
        "loop/done for for/repeat, approved/rejected for human.approval, error for the "
        'error branch>"}]}}. Rules: exactly one trigger node (manual, schedule, webhook, '
        "event or error) with no incoming edges; every other node reachable from it; use "
        "expressions like ={{ $trigger.<field> }} and ={{ $node.<id>.output.<field> }} in "
        "params; inside for/repeat bodies use $item / $index; reference credentials as "
        "={{ $secrets.<NAME> }}, never inline. Use ONLY these node types:\n"
        + _generation_catalog_context(catalog)
    )
    progress("calling", {
        "model": model or settings.default_model,
        "chain": failover_chain or "",
    })
    data, meta = await _llm_json_call(
        db, profile_id, {"model": model, "failover_chain": failover_chain}, system, prompt
    )
    progress("received", {"model": meta.get("model") or "", "cache": meta.get("_cache") or ""})
    if not isinstance(data, dict) or not isinstance(data.get("graph"), dict):
        raise ValueError("generation: model did not return {name, description, graph}")

    warnings: list[str] = []
    raw_graph = data["graph"]
    raw_nodes = [n for n in raw_graph.get("nodes") or [] if isinstance(n, dict)]
    kept_nodes = []
    for n in raw_nodes[: settings.graph_workflow_max_nodes]:
        if not n.get("id") or not n.get("type"):
            warnings.append("dropped a node without id/type")
            continue
        if n["type"] not in known:
            warnings.append(f"dropped node '{n['id']}': unknown type '{n['type']}'")
            continue
        kept_nodes.append(n)
    ids = {n["id"] for n in kept_nodes}
    kept_edges = []
    for i, e in enumerate(raw_graph.get("edges") or []):
        if not isinstance(e, dict) or e.get("source") not in ids or e.get("target") not in ids:
            warnings.append(f"dropped edge #{i + 1}: references a missing node")
            continue
        e.setdefault("id", f"e{i + 1}")
        kept_edges.append(e)

    graph = WorkflowGraph.model_validate({"nodes": kept_nodes, "edges": kept_edges})
    progress("normalized", {
        "nodes": len(graph.nodes), "edges": len(graph.edges), "dropped": len(warnings),
    })
    if not any(n.type in _TRIGGER_TYPES for n in graph.nodes):
        # A graph must start somewhere: prepend a manual trigger wired to the roots.
        warnings.append("no trigger node generated — a manual trigger was added")
        progress("trigger_added", {})
        targets = {e.target for e in graph.edges}
        roots = [n.id for n in graph.nodes if n.id not in targets]
        trigger_id = "trigger" if "trigger" not in ids else "trigger_start"
        graph.nodes.insert(0, GraphNode(id=trigger_id, type="manual", name="Start"))
        for i, root in enumerate(roots):
            graph.edges.append(GraphEdge(id=f"et{i + 1}", source=trigger_id, target=root))
    _layout_generated_nodes(graph)
    progress("layout", {})

    return {
        "name": str(data.get("name") or "Generated workflow")[:200],
        "description": str(data.get("description") or "")[:2000],
        "graph": graph,
        "warnings": warnings,
        "model": meta.get("model"),
    }


# ── explain / repair (Phase 45 — roadmap fase 13.2) ─────────────────────────

def _shallow_json_diff(old: dict, new: dict) -> list[dict]:
    """A flat {op, path, value} list for the review UI — not full RFC 6902 (no
    nested array ops), just enough for a human to see what a proposed params
    patch would add/remove/change before accepting it."""
    ops: list[dict] = []
    for key in sorted(set(old) | set(new)):
        if key not in old:
            ops.append({"op": "add", "path": f"/{key}", "value": new[key]})
        elif key not in new:
            ops.append({"op": "remove", "path": f"/{key}"})
        elif old[key] != new[key]:
            ops.append({"op": "replace", "path": f"/{key}", "value": new[key]})
    return ops


async def explain_run(db: aiosqlite.Connection, profile_id: str, run_id: str) -> dict:
    """Fase 13.2 — "explain / repair" for a failed run: the graph (as it ran),
    the failed node's catalog entry, input and error go to the LLM, which must
    reply with a plain-language explanation and, optionally, a corrected params
    object for that node. The proposed patch is NEVER applied automatically —
    the editor shows it as a diff the user accepts or discards."""
    from app.data.node_catalog import node_catalog

    run = await repo.get_run(db, run_id)
    if not run or run.profile_id != profile_id:
        raise ValueError("Run not found")
    node_id = await repo.first_error_node(db, run_id)
    if not node_id:
        raise ValueError("This run has no failed node to explain")

    node_runs = await repo.list_node_runs(db, run_id)
    nr = next((n for n in node_runs if n.node_id == node_id), None)
    graph_json = await repo.get_run_graph(db, run_id)
    graph = WorkflowGraph.model_validate(json.loads(graph_json)) if graph_json else None
    node = next((n for n in (graph.nodes if graph else []) if n.id == node_id), None)

    catalog = await node_catalog(db, profile_id)
    node_type = node.type if node else (nr.node_type if nr else "")
    type_info = next((t for t in catalog if t.type == node_type), None)

    system = (
        "You are a workflow debugging assistant for a visual DAG engine. You will be given "
        "one failed node (its type, catalog schema, current params, the input it received and "
        "the error it raised). Reply with ONLY a JSON object — no prose, no code fences — shaped "
        'exactly like {"explanation": "<1-3 sentences, plain language, root cause>", '
        '"proposed_params": {<corrected params for this node>} | null}. Only set "proposed_params" '
        "when you are reasonably confident of a concrete fix; set it to null rather than guess. "
        "Keep any keys you are not changing identical to the current params."
    )
    prompt = json.dumps(
        {
            "node_id": node_id,
            "node_type": node_type,
            "catalog_entry": type_info.model_dump() if type_info else None,
            "current_params": (node.params if node else {}) or {},
            "input": nr.input if nr else None,
            "error": (nr.error if nr else None) or run.error,
        },
        default=str,
        ensure_ascii=False,
    )
    data, meta = await _llm_json_call(db, profile_id, {}, system, prompt)
    if not isinstance(data, dict):
        raise ValueError("explain: model did not return a JSON object")

    proposed_params = data.get("proposed_params")
    patch = None
    if isinstance(proposed_params, dict):
        patch = _shallow_json_diff((node.params if node else {}) or {}, proposed_params)

    return {
        "node_id": node_id,
        "explanation": str(data.get("explanation") or "")[:4000],
        "proposed_params": proposed_params if isinstance(proposed_params, dict) else None,
        "patch": patch,
        "model": meta.get("model"),
    }


# ── Git sync of definitions (Phase 45 — roadmap fase 13.3) ──────────────────

class GitSyncError(RuntimeError):
    """A git subprocess failed — never raised out of the auto-push-on-save
    path (best-effort there); raised to the caller for the explicit /pull
    endpoint so the user sees why."""


def _git_workdir(wf_id: str):
    from pathlib import Path

    return Path(settings.graph_workflow_git_workdir).resolve() / wf_id


def _git_authed_url(url: str, token: str | None) -> str:
    """Inject an HTTPS access token as the URL's userinfo (GitHub/GitLab/etc.
    all accept `https://<token>@host/...`); untouched for SSH URLs or when no
    token is configured — those rely on the host's own git config/agent."""
    if not token or not url.startswith("https://"):
        return url
    return f"https://{token}@{url[len('https://') :]}"


def _run_git(args: list[str], cwd) -> None:
    import subprocess  # noqa: PLC0415 — only needed on the git-sync path

    result = subprocess.run(  # noqa: S603 — fixed 'git' binary, args are ours
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        timeout=settings.graph_workflow_git_timeout_seconds,
    )
    if result.returncode != 0:
        raise GitSyncError(f"git {' '.join(args)} failed: {result.stderr.strip()[:500]}")


def _git_sync_path(wf_id: str, subpath: str | None) -> str:
    return subpath or f"workflows/{wf_id}.json"


def _ensure_git_clone(workdir, url: str, branch: str) -> None:
    from pathlib import Path

    if (Path(workdir) / ".git").exists():
        _run_git(["fetch", "origin", branch], cwd=workdir)
        try:
            _run_git(["checkout", branch], cwd=workdir)
        except GitSyncError:
            _run_git(["checkout", "-b", branch, f"origin/{branch}"], cwd=workdir)
        return
    Path(workdir).mkdir(parents=True, exist_ok=True)
    try:
        _run_git(["clone", "--branch", branch, url, "."], cwd=workdir)
    except GitSyncError:
        # Branch doesn't exist yet on the remote (first push ever) — clone the
        # default branch and create ours locally.
        _run_git(["clone", url, "."], cwd=workdir)
        _run_git(["checkout", "-B", branch], cwd=workdir)


def _git_push_sync(*, url: str, branch: str, token: str | None, workdir, file_path: str, content: str, message: str) -> None:
    """Blocking git plumbing — always called via ``asyncio.to_thread``."""
    import subprocess  # noqa: PLC0415 — only needed on the git-sync path
    from pathlib import Path

    authed = _git_authed_url(url, token)
    _ensure_git_clone(workdir, authed, branch)
    full_path = Path(workdir) / file_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    _run_git(["add", file_path], cwd=workdir)
    status = subprocess.run(  # noqa: S603, S607
        ["git", "status", "--porcelain"], cwd=workdir, capture_output=True, text=True, check=False,
    )
    if not status.stdout.strip():
        return  # nothing changed — no empty commits
    _run_git(["-c", "user.email=sibyl@localhost", "-c", "user.name=Spice Sibyl", "commit", "-m", message], cwd=workdir)
    _run_git(["push", authed, f"HEAD:{branch}"], cwd=workdir)


def _git_pull_sync(*, url: str, branch: str, token: str | None, workdir, file_path: str) -> str | None:
    """Blocking git plumbing — returns the file's content at the branch HEAD,
    or None if it doesn't exist there yet."""
    from pathlib import Path

    authed = _git_authed_url(url, token)
    _ensure_git_clone(workdir, authed, branch)
    _run_git(["reset", "--hard", f"origin/{branch}"], cwd=workdir)
    full_path = Path(workdir) / file_path
    return full_path.read_text(encoding="utf-8") if full_path.exists() else None


async def _git_token(db: aiosqlite.Connection, profile_id: str, secret_name: str | None) -> str | None:
    if not secret_name:
        return None
    from app.services import vault_service

    encrypted = await repo.get_encrypted_secrets(db, profile_id)
    ciphertext = encrypted.get(secret_name)
    return vault_service.decrypt(ciphertext, settings.vault_secret_key) if ciphertext else None


async def git_sync_push_version(
    db: aiosqlite.Connection, wf, version: int, graph: WorkflowGraph, author: str,
) -> None:
    """Fase 13.3 — called right after a new version is snapshotted (create/update
    of the graph). Best-effort and silent: a broken remote must never fail the
    workflow save it is reacting to."""
    cfg = wf.git_sync
    if not cfg or not cfg.repo_url:
        return
    try:
        token = await _git_token(db, wf.profile_id, cfg.token_secret)
        envelope = {
            "kind": "spice-sibyl.graph-workflow",
            "schema_version": 1,
            "name": wf.name,
            "description": wf.description,
            "graph": redact_graph_for_export(graph),
            "workflow_version": version,
        }
        content = json.dumps(envelope, indent=2, ensure_ascii=False, default=str) + "\n"
        message = f"{wf.name} v{version} (by {author})"
        await asyncio.to_thread(
            _git_push_sync,
            url=cfg.repo_url, branch=cfg.branch, token=token,
            workdir=_git_workdir(wf.id), file_path=_git_sync_path(wf.id, cfg.subpath),
            content=content, message=message,
        )
        await repo.mark_git_synced(db, wf.id, int(time.time()))
    except Exception:  # noqa: BLE001 — auto-export must never break a save
        logger.exception("git-sync: push failed for workflow %s v%s", wf.id, version)


async def git_sync_pull(db: aiosqlite.Connection, wf) -> dict:
    """Fase 13.3 — pull the configured repo/branch and, if the file's graph
    differs from the latest known version, snapshot it as a new DRAFT version
    (never touches the live graph). Raises GitSyncError/ValueError to the
    caller — unlike push, a user-triggered pull should surface failures."""
    cfg = wf.git_sync
    if not cfg or not cfg.repo_url:
        raise ValueError("Git sync is not configured for this workflow")
    token = await _git_token(db, wf.profile_id, cfg.token_secret)
    content = await asyncio.to_thread(
        _git_pull_sync,
        url=cfg.repo_url, branch=cfg.branch, token=token,
        workdir=_git_workdir(wf.id), file_path=_git_sync_path(wf.id, cfg.subpath),
    )
    await repo.mark_git_synced(db, wf.id, int(time.time()))
    if content is None:
        return {"imported_versions": [], "unchanged": True}

    data = json.loads(content)
    raw_graph = data.get("graph") if isinstance(data, dict) else None
    if not isinstance(raw_graph, dict):
        raise ValueError("git-sync: the file at the configured path is not a valid workflow export")
    pulled_graph = WorkflowGraph.model_validate(raw_graph)

    versions = await repo.list_versions(db, wf.id)
    latest = versions[0]["version"] if versions else None
    if latest is not None:
        current_graph = await repo.get_version_graph(db, wf.id, latest)
        if current_graph is not None and current_graph.model_dump() == pulled_graph.model_dump():
            return {"imported_versions": [], "unchanged": True}

    new_version = await repo.add_draft_version(db, wf.id, pulled_graph)
    return {"imported_versions": [new_version], "unchanged": False}


# ── db / file nodes (Phase 35 — roadmap fase 4.2) ───────────────────────────

def _safe_workspace_path(raw, *, create_dirs: bool = False):
    """Resolve a node-supplied path INSIDE the workspace storage root
    (``GRAPH_WORKFLOW_FILES_DIR``). Absolute paths and ``..`` traversal that
    escape the root are rejected — file/db nodes can never touch the host FS."""
    from pathlib import Path

    rel = str(raw or "").strip()
    if not rel:
        raise ValueError("'path' is required")
    root = Path(settings.graph_workflow_files_dir).resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path {rel!r} escapes the workspace storage")
    if create_dirs:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


async def _exec_db_query(params: dict) -> dict:
    """Parameterised SQL. sqlite databases live inside the workspace storage;
    postgres connects via a DSN (typically ``={{ $secrets.PG_DSN }}``). Output:
    ``{rows, count, rowcount}`` (rows capped at 1000)."""
    query = str(params.get("query") or "").strip()
    if not query:
        raise ValueError("db.query: 'query' is required")
    args = params.get("params")
    if not isinstance(args, list):
        args = [] if args in (None, "") else [args]
    driver = str(params.get("driver") or "sqlite").strip().lower()

    if driver == "sqlite":
        path = _safe_workspace_path(params.get("database"), create_dirs=True)
        conn = await aiosqlite.connect(path)
        try:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(query, args)
            rows = [dict(r) for r in await cur.fetchmany(_DB_QUERY_MAX_ROWS)] if cur.description else []
            rowcount = cur.rowcount
            await conn.commit()
        finally:
            await conn.close()
        return {"rows": rows, "count": len(rows), "rowcount": rowcount}

    if driver == "postgres":
        dsn = str(params.get("dsn") or "").strip()
        if not dsn:
            raise ValueError("db.query: postgres needs a 'dsn' (use ={{ $secrets.<name> }})")
        try:
            import asyncpg  # noqa: PLC0415 — optional dependency
        except ImportError:
            raise RuntimeError(
                "db.query: postgres support requires the 'asyncpg' package in the backend image"
            ) from None
        conn = await asyncpg.connect(dsn=dsn, timeout=15)
        try:
            records = await conn.fetch(query, *args)
            rows = [dict(r) for r in records[:_DB_QUERY_MAX_ROWS]]
        finally:
            await conn.close()
        return {"rows": rows, "count": len(rows), "rowcount": len(rows)}

    raise ValueError(f"db.query: unknown driver {driver!r} (sqlite|postgres)")


def _file_format(params: dict, path) -> str:
    fmt = str(params.get("format") or "auto").strip().lower()
    if fmt != "auto":
        return fmt
    suffix = str(getattr(path, "suffix", "") or "").lower()
    return {".json": "json", ".csv": "csv"}.get(suffix, "text")


def _parse_structured(text: str, fmt: str, delimiter: str) -> dict:
    """Shared by file.read and file.parse: a text payload → structured output."""
    import csv
    import io

    if fmt == "json":
        try:
            return {"data": json.loads(text)}
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from None
    if fmt == "csv":
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter or ","))
        return {"rows": rows, "count": len(rows)}
    if fmt == "lines":
        lines = [ln for ln in text.splitlines() if ln.strip() != ""]
        return {"lines": lines, "count": len(lines)}
    return {"text": text, "size": len(text.encode("utf-8"))}


async def _exec_file_read(params: dict) -> dict:
    path = _safe_workspace_path(params.get("path"))
    if not path.is_file():
        raise FileNotFoundError(f"file.read: {params.get('path')!r} not found in the workspace storage")
    if path.stat().st_size > _FILE_MAX_BYTES:
        raise ValueError(f"file.read: file exceeds the {_FILE_MAX_BYTES // (1024 * 1024)} MB limit")
    encoding = str(params.get("encoding") or "utf-8")
    text = await asyncio.to_thread(path.read_text, encoding)
    fmt = _file_format(params, path)
    return {"path": str(params.get("path")), "format": fmt,
            **_parse_structured(text, fmt, str(params.get("delimiter") or ","))}


def _render_file_content(content, fmt: str, delimiter: str) -> str:
    import csv
    import io

    if fmt == "json" or (fmt == "text" and isinstance(content, (dict, list))):
        return json.dumps(content, indent=2, ensure_ascii=False, default=str)
    if fmt == "csv":
        rows = content if isinstance(content, list) else [content]
        if not rows:
            return ""
        buf = io.StringIO()
        if all(isinstance(r, dict) for r in rows):
            fieldnames: list[str] = []
            for r in rows:
                fieldnames.extend(k for k in r if k not in fieldnames)
            writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=delimiter or ",")
            writer.writeheader()
            writer.writerows(rows)
        else:
            plain = csv.writer(buf, delimiter=delimiter or ",")
            for r in rows:
                plain.writerow(r if isinstance(r, (list, tuple)) else [r])
        return buf.getvalue()
    return content if isinstance(content, str) else json.dumps(content, default=str, ensure_ascii=False)


async def _exec_file_write(params: dict, node_input) -> dict:
    path = _safe_workspace_path(params.get("path"), create_dirs=True)
    content = params.get("content")
    if content is None:
        content = node_input
    fmt = _file_format(params, path)
    text = _render_file_content(content, fmt, str(params.get("delimiter") or ","))
    if len(text.encode("utf-8")) > _FILE_MAX_BYTES:
        raise ValueError(f"file.write: content exceeds the {_FILE_MAX_BYTES // (1024 * 1024)} MB limit")
    append = _as_bool(params.get("append"))

    def _write() -> int:
        mode = "a" if append else "w"
        with open(path, mode, encoding=str(params.get("encoding") or "utf-8")) as fh:
            return fh.write(text)

    written = await asyncio.to_thread(_write)
    return {"path": str(params.get("path")), "format": fmt,
            "bytes_written": len(text.encode("utf-8")), "chars_written": written, "append": append}


def _exec_file_parse(params: dict, node_input) -> dict:
    """Parse an in-flight text payload (an http.request body, a tool result…)
    without touching disk. ``content`` defaults to the node input."""
    content = params.get("content")
    if content is None or content == "":
        content = node_input
    fmt = str(params.get("format") or "auto").strip().lower()
    if not isinstance(content, str):
        # Already-structured input passes through as parsed data.
        return {"data": content} if fmt in ("auto", "json") else {"rows": content if isinstance(content, list) else [content], "count": len(content) if isinstance(content, list) else 1}
    if fmt == "auto":
        stripped = content.strip()
        fmt = "json" if stripped[:1] in ("{", "[") else "csv" if ("," in stripped.splitlines()[0] if stripped else False) else "lines"
    return _parse_structured(content, fmt, str(params.get("delimiter") or ","))


# ── Phase 47 (roadmap fase 15) — connectors and multimodal nodes ────────────

def _connector_slack_post(p: dict) -> dict:
    return {
        "method": "POST", "url": "https://slack.com/api/chat.postMessage",
        "headers": {"Authorization": f"Bearer {p.get('token', '')}"},
        "body": {"channel": p.get("channel"), "text": p.get("text"),
                 **({"thread_ts": p["thread_ts"]} if p.get("thread_ts") else {})},
    }


def _connector_discord_post(p: dict) -> dict:
    return {"method": "POST", "url": str(p.get("webhook_url") or ""),
            "body": {"content": p.get("text"),
                     **({"username": p["username"]} if p.get("username") else {})}}


def _connector_github_issue(p: dict) -> dict:
    return {
        "method": "POST",
        "url": f"https://api.github.com/repos/{p.get('repo', '')}/issues",
        "headers": {"Authorization": f"Bearer {p.get('token', '')}",
                    "Accept": "application/vnd.github+json"},
        "body": {"title": p.get("title"), "body": p.get("body"),
                 **({"labels": p["labels"]} if p.get("labels") else {})},
    }


def _connector_gitlab_issue(p: dict) -> dict:
    from urllib.parse import quote

    base = str(p.get("base_url") or "https://gitlab.com").rstrip("/")
    project = quote(str(p.get("project") or ""), safe="")
    return {
        "method": "POST", "url": f"{base}/api/v4/projects/{project}/issues",
        "headers": {"PRIVATE-TOKEN": str(p.get("token") or "")},
        "body": {"title": p.get("title"), "description": p.get("body"),
                 **({"labels": p["labels"]} if p.get("labels") else {})},
    }


def _connector_jira_issue(p: dict) -> dict:
    import base64

    base = str(p.get("base_url") or "").rstrip("/")
    token = base64.b64encode(f"{p.get('email', '')}:{p.get('token', '')}".encode()).decode()
    return {
        "method": "POST", "url": f"{base}/rest/api/3/issue",
        "headers": {"Authorization": f"Basic {token}"},
        "body": {"fields": {
            "project": {"key": p.get("project_key")},
            "summary": p.get("summary"),
            "issuetype": {"name": p.get("issue_type") or "Task"},
            **({"description": p["description"]} if p.get("description") else {}),
        }},
    }


def _connector_sheets_append(p: dict) -> dict:
    rng = str(p.get("range") or "Sheet1!A1")
    return {
        "method": "POST",
        "url": (f"https://sheets.googleapis.com/v4/spreadsheets/"
                f"{p.get('spreadsheet_id', '')}/values/{rng}:append"),
        "query": {"valueInputOption": p.get("value_input_option") or "USER_ENTERED"},
        "headers": {"Authorization": f"Bearer {p.get('token', '')}"},
        "body": {"values": p.get("values") or []},
    }


def _connector_sheets_read(p: dict) -> dict:
    rng = str(p.get("range") or "Sheet1!A1:Z1000")
    return {
        "method": "GET",
        "url": (f"https://sheets.googleapis.com/v4/spreadsheets/"
                f"{p.get('spreadsheet_id', '')}/values/{rng}"),
        "headers": {"Authorization": f"Bearer {p.get('token', '')}"},
    }


# Registry of curated integrations (15.1). Each entry maps the connector's
# operation params to an http.request spec; auth values arrive already resolved
# from $secrets via the expression layer. Adding a service is a one-line entry
# — the dispatch, retry, node-test and pin machinery come for free.
_CONNECTORS: dict[str, callable] = {
    "slack.postMessage": _connector_slack_post,
    "discord.postMessage": _connector_discord_post,
    "github.createIssue": _connector_github_issue,
    "gitlab.createIssue": _connector_gitlab_issue,
    "jira.createIssue": _connector_jira_issue,
    "sheets.append": _connector_sheets_append,
    "sheets.read": _connector_sheets_read,
}


def _connector_request(operation: str, params: dict) -> dict:
    """Pure mapper (unit-testable, no I/O): connector operation + params → the
    http.request params the engine would issue. Raises for an unknown op."""
    builder = _CONNECTORS.get(operation)
    if builder is None:
        raise ValueError(
            f"connector: unknown operation '{operation}' "
            f"(known: {', '.join(sorted(_CONNECTORS))})"
        )
    spec = builder(params)
    # Carry through the shared http.request knobs so retry/rate-limit still apply.
    for passthrough in ("timeout", "allow_errors", "maxRequestsPerMinute"):
        if params.get(passthrough) is not None:
            spec[passthrough] = params[passthrough]
    return spec


async def _exec_connector(operation: str, params: dict, node_input) -> dict:
    """15.1 — execute a curated connector as an http.request. Output is the
    http.request output plus the ``operation`` that produced it."""
    spec = _connector_request(operation, params)
    out = await _exec_http_request(spec)
    out["operation"] = operation
    return out


def _ssh_host_allowed(host: str) -> bool:
    allowed = [h.strip().lower() for h in settings.graph_workflow_ssh_allowed_hosts.split(",") if h.strip()]
    return not allowed or host.lower() in allowed


async def _exec_ssh_exec(params: dict) -> dict:
    """15.2 — run a command on a remote host over SSH. Credentials (key or
    password) come from $secrets; the host must pass the per-instance allow-list.
    Output: {stdout, stderr, exit_code}. A non-zero exit raises unless
    ``allow_nonzero`` is set (so retry / On error apply)."""
    host = str(params.get("host") or "").strip()
    if not host:
        raise ValueError("ssh.exec: 'host' is required")
    if not _ssh_host_allowed(host):
        raise ValueError(f"ssh.exec: host '{host}' is not in GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS")
    command = str(params.get("command") or "").strip()
    if not command:
        raise ValueError("ssh.exec: 'command' is required")

    try:
        import paramiko  # noqa: PLC0415 — optional dependency
    except ImportError:
        raise RuntimeError("ssh.exec: the 'paramiko' package is required in the backend image") from None

    port = int(params.get("port") or 22)
    username = str(params.get("username") or "").strip()
    password = params.get("password")
    private_key = params.get("private_key")
    timeout = min(float(params.get("timeout") or settings.graph_workflow_ssh_timeout_seconds),
                  float(settings.graph_workflow_ssh_timeout_seconds))

    def _run() -> dict:
        import io

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = {"hostname": host, "port": port, "username": username, "timeout": timeout}
        if private_key:
            connect_kwargs["pkey"] = paramiko.RSAKey.from_private_key(io.StringIO(str(private_key)))
        elif password is not None:
            connect_kwargs["password"] = str(password)
        client.connect(**connect_kwargs)
        try:
            _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")[:_TOOL_RESULT_MAX_CHARS]
            err = stderr.read().decode("utf-8", errors="replace")[:_TOOL_RESULT_MAX_CHARS]
            code = stdout.channel.recv_exit_status()
            return {"stdout": out, "stderr": err, "exit_code": code}
        finally:
            client.close()

    result = await asyncio.to_thread(_run)
    if result["exit_code"] != 0 and not _as_bool(params.get("allow_nonzero")):
        raise RuntimeError(
            f"ssh.exec: '{command}' exited {result['exit_code']}: {result['stderr'][:300]}"
        )
    return result


async def _exec_browser(params: dict) -> dict:
    """15.3 — drive a headless browser (Playwright): open a URL, optionally wait
    for a selector, then extract text / an attribute / a rendered screenshot
    (saved to the workspace storage). Output depends on ``action``. Runs in a
    thread with a per-action timeout; a missing Playwright raises clearly."""
    url = str(params.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("browser: 'url' must be an http(s) URL")
    action = str(params.get("action") or "text").strip().lower()
    selector = str(params.get("selector") or "").strip()
    timeout_ms = int(min(float(params.get("timeout") or settings.graph_workflow_browser_timeout_seconds),
                         float(settings.graph_workflow_browser_timeout_seconds)) * 1000)

    screenshot_path = None
    if action == "screenshot":
        screenshot_path = _safe_workspace_path(
            params.get("screenshot_path") or f"browser/{uuid.uuid4().hex}.png", create_dirs=True,
        )

    def _run() -> dict:
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415 — optional dependency
        except ImportError:
            raise RuntimeError(
                "browser: the 'playwright' package (and a browser: playwright install chromium) "
                "is required in the backend image"
            ) from None

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                if selector:
                    page.wait_for_selector(selector, timeout=timeout_ms)
                target = page.locator(selector) if selector else None
                if action == "screenshot":
                    from pathlib import Path  # noqa: PLC0415

                    (target or page).screenshot(path=str(screenshot_path))
                    root = Path(settings.graph_workflow_files_dir).resolve()
                    return {"action": action, "url": url,
                            "path": str(screenshot_path.relative_to(root))}
                if action == "attribute":
                    attr = str(params.get("attribute") or "href")
                    return {"action": action, "url": url, "attribute": attr,
                            "value": (target or page).first.get_attribute(attr) if target else None}
                # default: extract text (of the selector, or the whole page)
                text = (target.first.inner_text() if target else page.inner_text("body"))
                return {"action": "text", "url": url,
                        "text": text[:_TOOL_RESULT_MAX_CHARS], "title": page.title()}
            finally:
                browser.close()

    return await asyncio.to_thread(_run)


async def _exec_doc_convert(params: dict, node_input) -> dict:
    """15.5 — convert a PDF/DOCX/HTML/… document from the workspace storage to
    markdown via markitdown (already in the backend image for the KB). Output:
    {markdown, chars, path}. ``path`` defaults to the node input."""
    raw = params.get("path")
    if raw in (None, "") and isinstance(node_input, str):
        raw = node_input
    path = _safe_workspace_path(raw)
    if not path.is_file():
        raise FileNotFoundError(f"doc.convert: {raw!r} not found in the workspace storage")
    if path.stat().st_size > _FILE_MAX_BYTES:
        raise ValueError(f"doc.convert: file exceeds the {_FILE_MAX_BYTES // (1024 * 1024)} MB limit")

    def _convert() -> str:
        from markitdown import MarkItDown  # noqa: PLC0415 — optional dependency

        return MarkItDown().convert(str(path)).text_content or ""

    try:
        markdown = await asyncio.to_thread(_convert)
    except ImportError:
        raise RuntimeError("doc.convert: the 'markitdown' package is required in the backend image") from None
    markdown = markdown[:_TOOL_RESULT_MAX_CHARS * 4]
    return {"path": str(raw), "markdown": markdown, "chars": len(markdown)}


# ── human-in-the-loop (Phase 35 — roadmap fase 4.4) ─────────────────────────

async def _notify_approval_request(
    db: aiosqlite.Connection, profile_id: str, approval, params: dict
) -> None:
    """Best-effort notification that a decision is awaited — a broken channel
    must never fail the node (the request row itself is the source of truth)."""
    from app.services import notification_service

    body = approval.message or "Apri Workflow → Esecuzioni per approvare o rifiutare."
    try:
        await notification_service.notify_web(db, profile_id, "workflow", approval.title, body)
    except Exception:  # noqa: BLE001
        logger.exception("human.approval: in-app notification failed for %s", approval.id)
    if _as_bool(params.get("telegram")):
        try:
            # Fase 7.5 — inline Approve/Reject buttons: the bot callback decides
            # the request exactly like POST /approvals/{id}/decision would.
            await notification_service.notify_telegram(
                db, profile_id, "workflow", f"{approval.title}\n{body}",
                buttons=[[
                    ("✅ Approve", f"wfap:a:{approval.id}"),
                    ("❌ Reject", f"wfap:r:{approval.id}"),
                ]],
            )
        except Exception:  # noqa: BLE001
            logger.exception("human.approval: telegram notification failed for %s", approval.id)


async def _wait_for_decision(db: aiosqlite.Connection, run_id: str, approval) -> WorkflowApprovalOut:
    """Suspend the run until ``approval`` is decided or ``timeout_at`` passes
    (roadmap fase 4.4; shared by human.approval/human.input/wait.event since
    fase 10). Flips the run to ``waiting`` then back to ``running`` around the
    poll — a cancelled/failing run overwrites this right after in _execute's
    handlers."""
    await repo.set_run_status(db, run_id, "waiting")
    _publish(run_id, {"kind": "run", "status": "waiting", "approval_id": approval.id})
    try:
        while True:
            current = await repo.get_approval(db, approval.id)
            if current is None:
                raise RuntimeError("waiting request row disappeared")
            if current.status != "pending":
                return current
            if current.timeout_at is not None and time.time() >= current.timeout_at:
                # First writer wins: the poll may race the decision endpoint.
                await repo.decide_approval(db, approval.id, status="expired")
                return await repo.get_approval(db, approval.id) or current
            await asyncio.sleep(_APPROVAL_POLL_SECONDS)
    finally:
        await repo.set_run_status(db, run_id, "running")
        _publish(run_id, {"kind": "run", "status": "running"})


async def _exec_human_approval(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, params: dict, ctx: dict
) -> tuple[object, list[str]]:
    """Suspend the run until a human decides (roadmap fase 4.4). Creates a
    ``workflow_approvals`` row (or re-attaches to the pending one after a
    restart — the fase 2.4 resume machinery re-executes this node), flips the
    run to ``waiting``, notifies, then polls the row until it is decided or
    ``timeout_at`` passes. Routes through the ``approved``/``rejected`` handle;
    a timeout follows ``onTimeout`` (reject | fail)."""
    run_id = ctx.get("_run_id")
    if not run_id:
        raise ValueError("human.approval: only runs inside a real execution (single-node test unsupported)")
    run = await repo.get_run(db, run_id)
    workflow_id = run.workflow_id if run else ""
    wf = await repo.get_workflow(db, workflow_id) if workflow_id else None

    approval = await repo.get_pending_approval(db, run_id, node.id)
    if approval is None:
        timeout_s = float(params.get("timeout") or 86400)
        timeout_s = max(1.0, min(timeout_s, float(settings.graph_workflow_approval_max_timeout)))
        title = str(params.get("title") or "").strip() or (
            f"Approvazione richiesta: {wf.name}" if wf else "Approvazione richiesta"
        )
        message = _notify_text(params)
        approval = await repo.create_approval(
            db, run_id, node.id, workflow_id, profile_id,
            title=title, message=message, timeout_at=int(time.time() + timeout_s),
        )
        await _notify_approval_request(db, profile_id, approval, params)

    current = await _wait_for_decision(db, run_id, approval)

    output = {
        "approved": current.status == "approved",
        "status": current.status,
        "comment": current.comment,
        "decided_by": current.decided_by,
        "approval_id": current.id,
        "title": current.title,
    }
    if current.status == "approved":
        return output, ["approved"]
    if current.status == "expired" and str(params.get("onTimeout") or "reject").lower() == "fail":
        raise RuntimeError("human.approval: request expired without a decision")
    return output, ["rejected"]


async def _exec_human_input(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, params: dict, ctx: dict
) -> tuple[object, list[str]]:
    """Suspend the run until a human fills a form (roadmap fase 10.1). Like
    ``human.approval`` but the request carries a JSON Schema (``schema`` param)
    and the run resumes with the submitted data as ``{data}``, validated by
    POST /approvals/{id}/submit before it is accepted. Routes through
    ``submitted``; a timeout follows ``onTimeout`` (branch | fail)."""
    run_id = ctx.get("_run_id")
    if not run_id:
        raise ValueError("human.input: only runs inside a real execution (single-node test unsupported)")
    run = await repo.get_run(db, run_id)
    workflow_id = run.workflow_id if run else ""
    wf = await repo.get_workflow(db, workflow_id) if workflow_id else None

    approval = await repo.get_pending_approval(db, run_id, node.id)
    if approval is None:
        timeout_s = float(params.get("timeout") or 86400)
        timeout_s = max(1.0, min(timeout_s, float(settings.graph_workflow_approval_max_timeout)))
        title = str(params.get("title") or "").strip() or (
            f"Input richiesto: {wf.name}" if wf else "Input richiesto"
        )
        form_schema = params.get("schema")
        if form_schema is not None and not isinstance(form_schema, dict):
            raise ValueError("human.input: 'schema' must be a JSON Schema object")
        message = _notify_text(params)
        approval = await repo.create_approval(
            db, run_id, node.id, workflow_id, profile_id,
            title=title, message=message, timeout_at=int(time.time() + timeout_s),
            kind="input", schema=form_schema,
        )
        await _notify_approval_request(db, profile_id, approval, params)

    current = await _wait_for_decision(db, run_id, approval)

    if current.status == "submitted":
        output = {
            "data": current.data, "status": current.status, "comment": current.comment,
            "decided_by": current.decided_by, "approval_id": current.id, "title": current.title,
        }
        return output, ["submitted"]
    if current.status == "expired" and str(params.get("onTimeout") or "branch").lower() == "fail":
        raise RuntimeError("human.input: request expired without a submission")
    return {"data": None, "status": current.status, "approval_id": current.id}, ["timeout"]


async def _exec_wait_event(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, params: dict, ctx: dict
) -> tuple[object, list[str]]:
    """Suspend the run until an external event with a matching correlation id
    arrives via POST /events/{correlation_id} (roadmap fase 10.2). The run
    resumes with the delivered payload as the node output, through ``main``;
    a timeout follows ``onTimeout`` (branch | fail)."""
    run_id = ctx.get("_run_id")
    if not run_id:
        raise ValueError("wait.event: only runs inside a real execution (single-node test unsupported)")
    correlation_id = str(params.get("correlationId") or "").strip()
    if not correlation_id:
        raise ValueError("wait.event: 'correlationId' is required")
    run = await repo.get_run(db, run_id)
    workflow_id = run.workflow_id if run else ""

    approval = await repo.get_pending_approval(db, run_id, node.id)
    if approval is None:
        timeout_s = float(params.get("timeout") or 86400)
        timeout_s = max(1.0, min(timeout_s, float(settings.graph_workflow_approval_max_timeout)))
        approval = await repo.create_approval(
            db, run_id, node.id, workflow_id, profile_id,
            title=f"In attesa dell'evento '{correlation_id}'", message="",
            timeout_at=int(time.time() + timeout_s),
            kind="event", correlation_id=correlation_id,
        )

    current = await _wait_for_decision(db, run_id, approval)

    if current.status == "delivered":
        payload = current.data if isinstance(current.data, dict) else {"data": current.data}
        return payload, ["main"]
    if current.status == "expired" and str(params.get("onTimeout") or "branch").lower() == "fail":
        raise RuntimeError("wait.event: timed out waiting for the correlated event")
    return {}, ["timeout"]


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


# ── external-world poll triggers (fase 6.2) ─────────────────────────────────

_WATCH_MAX_FIRES_PER_POLL = 10   # runs fired per file.watch poll pass
_EMAIL_MAX_MESSAGES = 5          # messages processed per email.inbound poll
_EMAIL_BODY_MAX_CHARS = 20000


async def _poll_watch_triggers(db: aiosqlite.Connection) -> None:
    """Fire due ``file.watch`` / ``email.inbound`` triggers (fase 6.2). Runs
    inside the schedule poll loop; ``next_run_at`` is reused as the next-poll
    timestamp (per-trigger ``interval`` floored by the global setting)."""
    due = await repo.list_due_poll_triggers(db, int(time.time()))
    for row in due:
        try:
            cfg = json.loads(row["config_json"])
        except (ValueError, TypeError):
            cfg = {}
        interval = max(
            int(cfg.get("interval") or 0), settings.graph_workflow_watch_poll_seconds
        )
        try:
            if row["type"] == "file.watch":
                fired = await _poll_file_watch(db, row, cfg)
            elif row["type"] == "email.inbound":
                fired = await _poll_email_inbound(db, row, cfg)
            elif row["type"] == "rss.read":
                fired = await _poll_rss_read(db, row, cfg)
            else:
                fired = await _poll_queue_consume(db, row, cfg)
            if fired:
                await _note_trigger_success(db, row["id"])
        except Exception as exc:  # noqa: BLE001 — one broken trigger must not stop the loop
            logger.exception("%s trigger poll failed id=%s", row["type"], row.get("id"))
            await _note_trigger_failure(
                db, row["id"], row["workflow_id"], row["wf_profile_id"], str(exc)
            )
        await repo.set_trigger_next_run(db, row["id"], int(time.time()) + interval)


async def _poll_file_watch(db: aiosqlite.Connection, row: dict, cfg: dict) -> bool:
    """One ``file.watch`` poll pass: diff the watched subfolder of the workspace
    storage against the snapshot in the trigger config and fire a run per
    created/modified file (``$trigger = {path, event, size}``). The very first
    poll only seeds the snapshot, so pre-existing files don't storm the engine."""
    from pathlib import Path

    root_dir = Path(settings.graph_workflow_files_dir).resolve()
    watch_dir = _safe_workspace_path(str(cfg.get("path") or ".").strip() or ".")
    pattern = str(cfg.get("pattern") or "**/*")
    events = cfg.get("events")
    if not isinstance(events, list) or not events:
        events = ["created", "modified"]

    def _scan() -> dict[str, list]:
        snapshot: dict[str, list] = {}
        if not watch_dir.is_dir():
            return snapshot
        for p in sorted(watch_dir.glob(pattern)):
            if p.is_file():
                st = p.stat()
                snapshot[str(p.relative_to(root_dir))] = [int(st.st_mtime), st.st_size]
        return snapshot

    snapshot = await asyncio.to_thread(_scan)
    previous = cfg.get("state")
    first_poll = not isinstance(previous, dict)
    changes: list[dict] = []
    if not first_poll:
        for rel, (mtime, size) in snapshot.items():
            if rel not in previous:
                changes.append({"path": rel, "event": "created", "size": size})
            elif previous[rel] != [mtime, size]:
                changes.append({"path": rel, "event": "modified", "size": size})

    fired = False
    for change in changes[:_WATCH_MAX_FIRES_PER_POLL]:
        if change["event"] not in events:
            continue
        await run_workflow(
            db, row["workflow_id"], row["wf_profile_id"],
            trigger_type="file.watch", trigger_payload=change,
        )
        fired = True

    if first_poll or snapshot != previous:
        await repo.update_trigger_config(db, row["id"], {**cfg, "state": snapshot})
    return fired or first_poll


def _parse_feed_entries(xml_text: str) -> list[dict]:
    """Dependency-free RSS/Atom parse (fase 15.4). Returns newest-first entries
    as ``{guid, title, link, published, summary}``; empty on malformed XML."""
    import xml.etree.ElementTree as ET

    def _strip_ns(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    entries: list[dict] = []
    # RSS: channel/item — Atom: feed/entry. Match by local name, namespace-agnostic.
    for node in root.iter():
        if _strip_ns(node.tag) not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        link = ""
        for child in node:
            name = _strip_ns(child.tag)
            if name == "link":
                # RSS puts the URL in text; Atom in the href attribute.
                link = (child.text or child.get("href") or "").strip() or link
            elif name in ("guid", "id") and "guid" not in fields:
                fields["guid"] = (child.text or "").strip()
            elif name == "title":
                fields["title"] = (child.text or "").strip()
            elif name in ("pubDate", "published", "updated") and "published" not in fields:
                fields["published"] = (child.text or "").strip()
            elif name in ("description", "summary") and "summary" not in fields:
                fields["summary"] = (child.text or "").strip()
        guid = fields.get("guid") or link or fields.get("title") or ""
        if not guid:
            continue
        entries.append({
            "guid": guid, "title": fields.get("title", ""), "link": link,
            "published": fields.get("published", ""), "summary": fields.get("summary", ""),
        })
    return entries


async def _poll_rss_read(db: aiosqlite.Connection, row: dict, cfg: dict) -> bool:
    """Fase 15.4 — one ``rss.read`` poll pass: fetch an RSS/Atom feed, dedup by
    guid against the trigger config and fire one run per new entry
    (``$trigger = {title, link, published, summary, guid}``). The first poll only
    seeds the seen-set so a backlog doesn't storm the engine."""
    import httpx

    url = str(cfg.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("rss.read: 'url' (feed URL) is required in the trigger config")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "spice-sibyl-rss/1.0"})
    resp.raise_for_status()
    entries = _parse_feed_entries(resp.text)

    seen = cfg.get("state") if isinstance(cfg.get("state"), list) else None
    first_poll = seen is None
    seen_set = set(seen or [])

    fired = False
    new_guids: list[str] = []
    # Newest first, capped so a burst never storms the engine.
    for entry in entries[: settings.graph_workflow_rss_max_entries]:
        guid = entry["guid"]
        if guid in seen_set:
            continue
        new_guids.append(guid)
        if not first_poll:
            await run_workflow(
                db, row["workflow_id"], row["wf_profile_id"],
                trigger_type="rss.read", trigger_payload=entry,
            )
            fired = True

    if new_guids or first_poll:
        # Keep the seen-set bounded: remember the guids currently in the feed
        # plus any freshly fired, most-recent first.
        merged = new_guids + [g for g in (seen or []) if g not in set(new_guids)]
        await repo.update_trigger_config(
            db, row["id"], {**cfg, "state": merged[: settings.graph_workflow_rss_max_entries * 5]},
        )
    return fired or first_poll


def _email_text(msg) -> str:
    """The text/plain body of a parsed email message (best-effort decode)."""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/plain" and part.get("Content-Disposition") is None:
            payload = part.get_payload(decode=True)
            if payload is not None:
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")[:_EMAIL_BODY_MAX_CHARS]
    payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")[:_EMAIL_BODY_MAX_CHARS]
    return ""


async def _poll_email_inbound(db: aiosqlite.Connection, row: dict, cfg: dict) -> bool:
    """One ``email.inbound`` poll pass: fetch UNSEEN messages over IMAP
    (credentials from ``$secrets``), apply the sender/subject filters and fire a
    run per matching message with ``$trigger = {from, subject, body, attachments}``
    — attachments land in the workspace storage, readable with ``file.read``."""
    import email as email_lib
    import imaplib
    import re as re_mod
    from email.header import decode_header, make_header
    from pathlib import Path

    host = str(cfg.get("host") or "").strip()
    if not host:
        raise ValueError("email.inbound: 'host' (IMAP server) is required in the trigger config")
    port = int(cfg.get("port") or 993)
    folder = str(cfg.get("folder") or "INBOX")
    username = str(cfg.get("username") or "").strip()
    secrets_ctx = await _secrets_context(db, row["wf_profile_id"])
    user_secret = str(cfg.get("username_secret") or "").strip()
    if user_secret:
        username = secrets_ctx.get(user_secret, username)
    password_secret = str(cfg.get("password_secret") or "").strip()
    if not password_secret:
        raise ValueError("email.inbound: 'password_secret' (name of a workflow secret) is required")
    password = secrets_ctx.get(password_secret)
    if not username or password is None:
        raise ValueError("email.inbound: username/password secret not configured")

    def _fetch() -> list:
        conn = imaplib.IMAP4_SSL(host, port, timeout=30)
        try:
            conn.login(username, password)
            conn.select(folder)
            _typ, data = conn.search(None, "UNSEEN")
            ids = (data[0].split() if data and data[0] else [])[:_EMAIL_MAX_MESSAGES]
            raw_messages = []
            for mid in ids:
                _typ, msg_data = conn.fetch(mid, "(RFC822)")  # marks the message \Seen
                for chunk in msg_data or []:
                    if isinstance(chunk, tuple) and len(chunk) > 1:
                        raw_messages.append(chunk[1])
                        break
            return raw_messages
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    raw_messages = await asyncio.to_thread(_fetch)
    if not raw_messages:
        return False

    def _hdr(msg, name: str) -> str:
        raw = msg.get(name) or ""
        try:
            return str(make_header(decode_header(raw)))
        except Exception:  # noqa: BLE001 — a malformed header stays raw
            return raw

    from_filter = str(cfg.get("from") or "").strip().lower()
    subject_filter = str(cfg.get("subject") or "").strip().lower()
    attach_root = Path(settings.graph_workflow_files_dir).resolve() / "email_attachments" / row["id"]

    fired = False
    for raw in raw_messages:
        msg = email_lib.message_from_bytes(raw)
        sender = _hdr(msg, "From")
        subject = _hdr(msg, "Subject")
        if from_filter and from_filter not in sender.lower():
            continue
        if subject_filter and subject_filter not in subject.lower():
            continue

        attachments: list[str] = []

        def _save_attachments() -> None:
            for part in msg.walk():
                if str(part.get("Content-Disposition") or "").startswith("attachment"):
                    filename = part.get_filename() or "attachment.bin"
                    filename = re_mod.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120]
                    payload = part.get_payload(decode=True)
                    if payload is None or len(payload) > _FILE_MAX_BYTES:
                        continue
                    attach_root.mkdir(parents=True, exist_ok=True)
                    target = attach_root / f"{int(time.time())}_{filename}"
                    target.write_bytes(payload)
                    attachments.append(
                        str(target.relative_to(Path(settings.graph_workflow_files_dir).resolve()))
                    )

        await asyncio.to_thread(_save_attachments)
        await run_workflow(
            db, row["workflow_id"], row["wf_profile_id"],
            trigger_type="email.inbound",
            trigger_payload={
                "from": sender,
                "subject": subject,
                "body": _email_text(msg),
                "attachments": attachments,
            },
        )
        fired = True
    return fired


# ── Phase 49 (roadmap fase 17.1) — calendars, windows and blackout ──────────

_BLACKOUT_DEFER_SECONDS = 300  # a deferred schedule re-checks this often until the window clears


def _schedule_tz(cfg: dict, default_tz):
    """Per-schedule timezone (fase 17.1): ``cfg.tz``/``cfg.timezone`` if valid,
    else the global default. So one workflow can carry schedules in several zones."""
    from zoneinfo import ZoneInfo

    name = (cfg or {}).get("tz") or (cfg or {}).get("timezone")
    if name:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 — an unknown tz name falls back, never raises
            logger.warning("schedule: unknown timezone %r, using default", name)
    return default_tz


def _parse_hhmm(value) -> tuple[int, int] | None:
    try:
        h, m = str(value).split(":", 1)
        h_i, m_i = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if 0 <= h_i < 24 and 0 <= m_i < 60:
        return h_i, m_i
    return None


def _in_blackout_window(fire_dt, windows) -> bool:
    """True when ``fire_dt`` (already in the schedule's tz) falls inside any
    window. A window is {start:"HH:MM", end:"HH:MM", days:[0-6]?}; an end <= start
    wraps past midnight. ``days`` (Mon=0) restricts it to specific weekdays."""
    minute_of_day = fire_dt.hour * 60 + fire_dt.minute
    for w in windows or []:
        if not isinstance(w, dict):
            continue
        days = w.get("days")
        if days and fire_dt.weekday() not in days:
            continue
        start = _parse_hhmm(w.get("start"))
        end = _parse_hhmm(w.get("end"))
        if start is None or end is None:
            continue
        s = start[0] * 60 + start[1]
        e = end[0] * 60 + end[1]
        if s <= e:
            if s <= minute_of_day < e:
                return True
        elif minute_of_day >= s or minute_of_day < e:  # overnight window
            return True
    return False


def _schedule_blocked(cfg: dict, blackout: dict, fire_ts: int, default_tz) -> tuple[bool, str]:
    """Fase 17.1 — decide whether a due schedule may run at ``fire_ts``.

    Returns ``(blocked, on_conflict)``. Blocked when the fire date is a holiday
    skip date (schedule- or workflow-level ``skip_dates``) or falls in a workflow
    blackout window. ``on_conflict`` ("skip" default, or "defer") tells the caller
    whether to drop this beat or retry once the window clears."""
    from datetime import datetime

    tz = _schedule_tz(cfg, default_tz)
    fire_dt = datetime.fromtimestamp(fire_ts, tz)
    on_conflict = (blackout or {}).get("on_conflict", "skip")
    skip_dates = set((cfg or {}).get("skip_dates") or []) | set((blackout or {}).get("skip_dates") or [])
    if fire_dt.strftime("%Y-%m-%d") in skip_dates:
        return True, on_conflict
    if _in_blackout_window(fire_dt, (blackout or {}).get("windows") or []):
        return True, on_conflict
    return False, on_conflict


# ── Phase 49 (roadmap fase 17.2/17.5) — SLA monitors and notification digest ─

async def _deliver_alert(
    db: aiosqlite.Connection, profile_id: str, channels, title: str, body: str
) -> None:
    """Route an SLA/digest message to each configured channel (fase 17.2/17.5).
    ``inapp`` uses the web notification stream; ``telegram`` the linked chat.
    Best-effort per channel: one failing channel never blocks the others or the
    scheduler tick."""
    from app.services import notification_service

    for ch in (channels or ["inapp"]):
        try:
            if ch == "telegram":
                await notification_service.notify_telegram(db, profile_id, "workflow", f"{title}\n{body}")
            else:
                await notification_service.notify_web(db, profile_id, "workflow", title, body)
        except Exception:  # noqa: BLE001 — alerting must never break the sweep
            logger.exception("alert delivery failed channel=%s profile=%s", ch, profile_id)


async def check_sla_monitors() -> None:
    """Fase 17.2 — one SLA sweep (called from the scheduler tick). Raises a
    one-time alert for every run that overran ``sla.max_duration_s`` and every
    enabled schedule overdue past ``sla.missed_grace_s`` (the run never started),
    then marks each so it is not re-alerted."""
    db = await _connect()
    try:
        now = int(time.time())
        for r in await repo.list_runs_over_duration(db, now):
            sla = r.get("sla") or {}
            await _deliver_alert(
                db, r["profile_id"], sla.get("channels") or ["inapp"],
                "SLA superata: run troppo lenta",
                f"La run del workflow '{r['workflow_name']}' ha superato la soglia di "
                f"{sla.get('max_duration_s')}s (durata {r['elapsed_s']}s, stato {r['status']}).",
            )
            await repo.mark_run_sla_alerted(db, r["id"])
        for t in await repo.list_overdue_schedule_triggers(db, now):
            sla = t.get("sla") or {}
            await _deliver_alert(
                db, t["wf_profile_id"], sla.get("channels") or ["inapp"],
                "SLA superata: schedule mancato",
                f"La run pianificata del workflow '{t['workflow_name']}' è in ritardo di "
                f"{t['overdue_s']}s e non è mai partita.",
            )
            await repo.mark_trigger_sla_alerted(db, t["id"], now)
    finally:
        await db.close()


async def _record_run_outcome(
    db: aiosqlite.Connection, workflow_id: str, profile_id: str, run_id: str, status: str
) -> None:
    """Fase 17.5 — buffer a terminal-run notification when the workflow opted into
    digest mode. A no-op when digest is off, so this never adds noise to workflows
    that did not ask for it (digest is strictly opt-in)."""
    if status not in ("completed", "failed", "cancelled"):
        return
    try:
        wf = await repo.get_workflow(db, workflow_id)
    except Exception:  # noqa: BLE001
        return
    if wf is None:
        return
    dcfg = (wf.notify or {}).get("digest") or {}
    if not dcfg.get("enabled"):
        return
    channel = dcfg.get("channel") or "inapp"
    try:
        await repo.enqueue_digest(db, workflow_id, profile_id, channel, status, run_id)
    except Exception:  # noqa: BLE001 — digest buffering must never fail the run
        logger.exception("digest enqueue failed run=%s", run_id)


async def flush_notification_digests() -> None:
    """Fase 17.5 — one digest-flush pass (called from the scheduler tick). A
    (workflow, channel) bucket whose oldest entry is older than the workflow's
    ``notify.digest.interval_s`` is delivered as a single summary (counts by
    outcome) and cleared."""
    db = await _connect()
    try:
        now = int(time.time())
        for g in await repo.list_digest_groups(db):
            try:
                notify = json.loads(g.get("notify_json") or "{}")
            except (ValueError, TypeError):
                notify = {}
            dcfg = (notify or {}).get("digest") or {}
            raw_interval = dcfg.get("interval_s")
            interval = 3600 if raw_interval is None else int(raw_interval)
            if now - g["oldest"] < interval:
                continue
            counts = await repo.digest_outcome_counts(db, g["workflow_id"], g["channel"])
            parts = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
            await _deliver_alert(
                db, g["profile_id"], [g["channel"]],
                f"Riepilogo workflow — {g['workflow_name']}",
                f"{g['total']} run negli ultimi {interval // 60} min — {parts}.",
            )
            await repo.clear_digest(db, g["workflow_id"], g["channel"])
    finally:
        await db.close()


# ── Phase 49 (roadmap fase 17.4) — run comparison ───────────────────────────

def _json_equal(a, b) -> bool:
    try:
        return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return a == b


async def compare_runs(db: aiosqlite.Connection, run_a_id: str, run_b_id: str):
    """Fase 17.4 — side-by-side diff of two runs of the same workflow: per-node
    status, duration and output, plus the first node (in run A's execution order)
    that diverges. Answers "why did it work yesterday?"."""
    from app.schemas.graph_workflows import RunCompareNode, RunCompareOut

    a = await repo.get_run(db, run_a_id)
    b = await repo.get_run(db, run_b_id)
    if a is None or b is None:
        raise ValueError("run not found")
    if a.workflow_id != b.workflow_id:
        raise ValueError("runs belong to different workflows")

    nodes_a = {n.node_id: n for n in await repo.list_node_runs(db, run_a_id)}
    nodes_b = {n.node_id: n for n in await repo.list_node_runs(db, run_b_id)}
    order = list(nodes_a.keys()) + [k for k in nodes_b if k not in nodes_a]

    def _dur(n) -> int | None:
        if n and n.started_at and n.finished_at:
            return int((n.finished_at - n.started_at) * 1000)
        return None

    rows: list = []
    first_divergent: str | None = None
    for nid in order:
        na = nodes_a.get(nid)
        nb = nodes_b.get(nid)
        status_a = na.status if na else None
        status_b = nb.status if nb else None
        out_a = na.output if na else None
        out_b = nb.output if nb else None
        equal = status_a == status_b and _json_equal(out_a, out_b)
        if not equal and first_divergent is None:
            first_divergent = nid
        rows.append(RunCompareNode(
            node_id=nid,
            node_type=(na.node_type if na else (nb.node_type if nb else None)),
            status_a=status_a, status_b=status_b,
            duration_ms_a=_dur(na), duration_ms_b=_dur(nb),
            output_equal=equal,
            # Only carry the payloads when they differ, to keep the diff light.
            output_a=None if equal else out_a,
            output_b=None if equal else out_b,
        ))

    return RunCompareOut(
        workflow_id=a.workflow_id,
        run_a=run_a_id, run_b=run_b_id,
        status_a=a.status, status_b=b.status,
        duration_ms_a=int((a.updated_at - a.created_at) * 1000),
        duration_ms_b=int((b.updated_at - b.created_at) * 1000),
        first_divergent_node=first_divergent,
        nodes=rows,
    )


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
                        tz_sched = _schedule_tz(cfg, tz)
                        # Fase 17.1 — a due schedule inside a blackout window / on a
                        # holiday skip date is not run: "skip" advances to the next
                        # recurrence, "defer" retries once the window clears.
                        try:
                            blackout = json.loads(row.get("wf_blackout") or "{}")
                        except (ValueError, TypeError):
                            blackout = {}
                        blocked, on_conflict = _schedule_blocked(cfg, blackout, int(time.time()), tz)
                        if blocked:
                            if on_conflict == "defer":
                                await repo.set_trigger_next_run(
                                    db, row["id"], int(time.time()) + _BLACKOUT_DEFER_SECONDS
                                )
                            else:
                                recurrence = cfg.get("recurrence", "once")
                                nxt = reminder_parsing.compute_next_fire(recurrence, int(time.time()), tz_sched)
                                await repo.set_trigger_next_run(db, row["id"], nxt)
                                if nxt is None:
                                    await repo.set_trigger_enabled(db, row["id"], False)
                            continue
                        await run_workflow(
                            db,
                            row["workflow_id"],
                            row["wf_profile_id"],
                            trigger_type="schedule",
                            trigger_payload={"schedule": cfg},
                            # Fase 7.2 — a schedule may pin the environment its
                            # runs execute in ({"environment": "prod"}).
                            environment=cfg.get("environment"),
                        )
                        # Recompute next_run_at from the recurrence spec, in the
                        # schedule's own timezone (fase 17.1).
                        recurrence = cfg.get("recurrence", "once")
                        nxt = reminder_parsing.compute_next_fire(recurrence, int(time.time()), tz_sched)
                        await repo.set_trigger_next_run(db, row["id"], nxt)
                        await _note_trigger_success(db, row["id"])
                        if nxt is None:
                            await repo.set_trigger_enabled(db, row["id"], False)
                    except Exception as exc:
                        logger.exception("schedule trigger firing failed id=%s", row.get("id"))
                        await _note_trigger_failure(
                            db, row["id"], row["workflow_id"], row["wf_profile_id"], str(exc)
                        )
                # Fase 6.2 — the same loop also drives the poll-based
                # file.watch / email.inbound triggers (per-trigger cadence).
                await _poll_watch_triggers(db)
            finally:
                await db.close()
            # Fase 8.3 — reap step-debug sessions left paused past the timeout
            # (its own connection: keep the schedule tick's failure isolated).
            try:
                await cancel_stale_debug_runs()
            except Exception:
                logger.exception("stale debug-run sweep failed")
            # Fase 9.3 — purge idle chat sessions past their TTL.
            try:
                await purge_stale_chat_sessions()
            except Exception:
                logger.exception("stale chat-session purge failed")
            # Fase 12.2 — purge terminal runs past their retention window.
            try:
                await purge_old_runs()
            except Exception:
                logger.exception("run-retention purge failed")
            # Fase 16.1/16.2 — reclaim expired state keys and dedup entries.
            try:
                await purge_expired_state_and_dedup()
            except Exception:
                logger.exception("state/dedup purge failed")
            # Fase 17.2 — SLA sweep: alert on overrunning runs / missed beats.
            try:
                await check_sla_monitors()
            except Exception:
                logger.exception("SLA monitor sweep failed")
            # Fase 17.5 — flush any notification digests whose window has elapsed.
            try:
                await flush_notification_digests()
            except Exception:
                logger.exception("notification digest flush failed")
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
                    wf = await repo.get_workflow(db, row["workflow_id"])
                    if wf is None:
                        continue
                    # Fase 16.2/16.4 — event triggers honour dedupKey + priority too.
                    trigger = SimpleNamespace(id=row["id"], config=json.loads(row["config_json"] or "{}"))
                    await run_from_trigger(
                        db, trigger, wf, "event",
                        {"event": event_type, **(payload or {})},
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
