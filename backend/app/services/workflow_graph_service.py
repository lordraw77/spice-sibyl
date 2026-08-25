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
from app.services import coordination
from app.workflow import registry
from app.workflow import nodes as _nodes  # noqa: F401 — importing registers node families
from app.workflow.bus import publish as _publish, subscribe, unsubscribe  # noqa: F401 — SSE bus (§3)
from app.workflow.context import (
    _as_bool,
    _FILE_MAX_BYTES,
    _MAX_LOOP_ITERATIONS,
    _MAX_SUBWORKFLOW_DEPTH,
    _safe_workspace_path,
    _TOOL_RESULT_MAX_CHARS,
    _WAIT_MAX_SECONDS,
)
from app.workflow.registry import DispatchCtx
# Extracted node-family impls re-exported from the engine module. Some are used
# by the engine directly (stateless remote-runner path; notify.webhook →
# http.request); the rest are re-exported as a compatibility shim so existing
# imports/tests referencing ``workflow_graph_service._exec_*`` keep working.
from app.workflow.nodes.io import (  # noqa: F401
    _connector_request,
    _exec_browser,
    _exec_connector,
    _exec_db_query,
    _exec_doc_convert,
    _exec_file_parse,
    _exec_file_read,
    _exec_file_write,
    _exec_http_request,
    _exec_ssh_exec,
    _host_rate_limit,
    _parse_rate_limits,
    _rate_hits,
    _rate_limit_admit,
    _ssh_host_allowed,
)
from app.workflow.nodes.llm import (  # noqa: F401
    _cached_complete,
    _candidate_models,
    _classify_categories,
    _exec_llm_agent,
    _exec_llm_classify,
    _exec_llm_completion,
    _exec_llm_extract,
    _exec_llm_judge,
    _extract_usage,
    _full_tool_definitions,
    _judge_scale_max,
    _llm_json_call,
    _parse_llm_json,
)
from app.workflow.nodes.logic import _exec_aggregate, _exec_batch, _exec_filter, _exec_switch
from app.workflow.nodes.messaging import (  # noqa: F401
    _exec_kb_search,
    _exec_notify_email,
    _exec_notify_inapp,
    _exec_notify_telegram,
    _exec_notify_webhook,
    _exec_telegram_delete,
    _exec_telegram_edit,
    _exec_telegram_send,
    _exec_telegram_send_media,
    _notify_text,
    _resolve_chat_id,
    _telegram_bot,
)
from app.workflow.nodes.hitl import (  # noqa: F401
    _exec_human_approval,
    _exec_human_input,
    _exec_telegram_ask,
    _exec_wait_event,
    _notify_approval_request,
    _wait_for_decision,
)
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
# Shared limits now live in app/workflow/context.py (imported below) so node
# family modules can use them without importing the engine.
_RETRY_MAX_BACKOFF_SECONDS = 60.0  # cap per pause, even with exponential growth
_SCHEDULE_POLL_SECONDS = 20
# Phase 35 (roadmap fase 4) — new node bounds.
# _APPROVAL_POLL_SECONDS now lives in app/workflow/nodes/hitl.py (human-in-the-loop family)
# _DB_QUERY_MAX_ROWS / _FILE_MAX_BYTES now live in app/workflow/context.py
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

# Fire-and-forget run tasks so they aren't garbage-collected.
_run_tasks: set[asyncio.Task] = set()
# run id → its task, so a run can be cancelled from the registry.
_tasks_by_run: dict[str, asyncio.Task] = {}
_poll_task: asyncio.Task | None = None


# ── SSE bus ─────────────────────────────────────────────────────────────────
# The in-memory bus (subscribe/unsubscribe/_publish, imported above) now lives
# in app/workflow/bus.py (roadmap §3) so node families can publish run-lifecycle
# events without importing the engine. Re-exported here for existing call sites
# (e.g. graph_workflows.py: engine.subscribe / engine.unsubscribe).


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


# set / if / switch / merge / filter / aggregate / batch → app/workflow/nodes/logic.py


@registry.node("code")
async def _h_code(c: DispatchCtx):
    return await _exec_code(c.params, c.node_input, c.ctx), ["main"]


@registry.node("wait")
async def _h_wait(c: DispatchCtx):
    return await _exec_wait(c.params), ["main"]


# http.request / db.query / file.* / connector.* / ssh.exec / browser / doc.convert
#   → app/workflow/nodes/io.py

# -- subworkflow & callable workflows --

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


# chat.reply / kb.search / notify.* / telegram.send/sendMedia/editMessage/deleteMessage
#   → app/workflow/nodes/messaging.py

# llm.completion / llm.agent / llm.classify / llm.extract / llm.judge
#   → app/workflow/nodes/llm.py

# telegram.ask / human.approval / human.input / wait.event (run-suspending
# human-in-the-loop family) → app/workflow/nodes/hitl.py
# custom.* (Custom Node SDK, Phase 51) → app/workflow/nodes/custom.py


@registry.node("queue.publish")
async def _h_queue_publish(c: DispatchCtx):
    return await _exec_queue_publish(c.params, c.node_input), ["main"]


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


# _exec_telegram_ask now lives in app/workflow/nodes/hitl.py (run-suspending family).


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


# ── human-in-the-loop (Phase 35 — roadmap fase 4.4) ─────────────────────────

# The human-in-the-loop family (_notify_approval_request, _wait_for_decision,
# _exec_human_approval, _exec_human_input, _exec_wait_event) now lives in
# app/workflow/nodes/hitl.py; re-exported near the node-family imports above so
# call sites referencing workflow_graph_service._exec_* / ._wait_for_decision
# keep working.


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

_was_scheduler_leader = True


async def _hold_scheduler_lease() -> bool:
    """Whether this instance may fire schedules on this tick.

    True immediately when leader election is disabled. Otherwise it takes or
    renews the lease, logging only on transitions so a standby instance does
    not narrate every poll.
    """
    global _was_scheduler_leader
    if not settings.scheduler_leader_election:
        return True
    db = await _connect()
    try:
        is_leader = await coordination.acquire(
            db, coordination.SCHEDULER, settings.scheduler_lease_ttl_seconds
        )
    except Exception:  # noqa: BLE001 — a lease error must not stop the loop
        logger.exception("scheduler lease acquisition failed; standing by this tick")
        return False
    finally:
        await db.close()

    if is_leader and not _was_scheduler_leader:
        logger.info("workflow scheduler: took the lease, firing schedules")
    elif not is_leader and _was_scheduler_leader:
        logger.info("workflow scheduler: standing by, another instance holds the lease")
    _was_scheduler_leader = is_leader
    return is_leader


async def _poll_loop() -> None:
    from app.services import reminder_parsing
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.timezone) if getattr(settings, "timezone", None) else ZoneInfo("UTC")
    while True:
        # Roadmap v2 § 3, P2 — with more than one instance against the same
        # database, each would see the same due trigger and start the same run.
        # Only the lease holder fires; a single instance always wins the lease,
        # so single-node behaviour is unchanged.
        if not await _hold_scheduler_lease():
            await asyncio.sleep(_SCHEDULE_POLL_SECONDS)
            continue
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
    # Hand the duty over now instead of making the next instance wait out the
    # lease TTL. Best-effort: a crash skips this and the lease just expires.
    if settings.scheduler_leader_election:
        try:
            db = await _connect()
            try:
                await coordination.release(db, coordination.SCHEDULER)
            finally:
                await db.close()
        except Exception:  # noqa: BLE001 — shutdown must not fail on this
            logger.exception("scheduler lease release failed")


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
