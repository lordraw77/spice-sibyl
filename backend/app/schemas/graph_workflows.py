"""
Phase 29 — visual node-graph workflow engine schemas.

A *graph workflow* is a deterministic DAG of typed nodes: a trigger feeds one or
more downstream nodes connected by edges. ``graph_json`` ({nodes, edges}) is the
source of truth. This coexists with the Phase 18 agent runs (``schemas/workflows.py``);
the agent loop is exposed here as the ``llm.agent`` node type.
"""

from typing import Any

from pydantic import BaseModel, Field

# (queued →) pending → running (⇄ waiting) → completed | failed | cancelled —
# `queued` is entered when the workflow's max_concurrent_runs slots are all taken
# (Phase 33); `waiting` while a human.approval node awaits a decision (Phase 35).
# `paused` (Phase 40 — roadmap fase 8.3) is entered by a step-debug run before
# each node; it advances via POST /runs/{id}/debug.
GRAPH_RUN_STATUSES = ("queued", "pending", "running", "waiting", "paused", "completed", "failed", "cancelled")
# Phase 42 (roadmap fase 10) adds "submitted" (human.input) and "delivered" (wait.event)
# to the approval-request lifecycle alongside the fase 4.4 approve/reject states.
APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "cancelled", "submitted", "delivered")
NODE_RUN_STATUSES = ("pending", "running", "ok", "error", "skipped")
# `error` (Phase 33 — roadmap fase 2.5) fires the workflow when another run fails;
# `success` (Phase 38 — roadmap fase 6.1) when another run completes; `file.watch`
# / `email.inbound` (fase 6.2) are poll-based external-world triggers; `chat`
# (Phase 41 — roadmap fase 9.3) turns the workflow into a chatbot: one run per
# conversation message, $trigger = {session_id, message, history}.
# `queue.consume` (Phase 46 — roadmap fase 14.4) is another poll-based
# external-world trigger, like file.watch/email.inbound: it consumes messages
# off a configured QueueDriver topic instead of watching files/IMAP.
# `rss.read` (Phase 47 — roadmap fase 15.4) polls an RSS/Atom feed and fires one
# run per new entry (deduped by guid), $trigger = {title, link, published, summary, guid}.
TRIGGER_TYPES = (
    "manual", "schedule", "webhook", "event", "error", "success",
    "file.watch", "email.inbound", "chat", "queue.consume", "rss.read",
)


class GraphNode(BaseModel):
    """One node in the graph. ``type`` selects the executor; ``params`` are
    resolved through the expression resolver immediately before execution."""

    id: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)  # manual|schedule|webhook|event|tool.<name>|set|if|switch|merge|filter|code|http.request|subworkflow|llm.completion|llm.agent
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=dict)  # {x, y} for the canvas
    retry: int = Field(default=0, ge=0, le=10)
    backoff: float = Field(default=0.0, ge=0.0, le=60.0)  # base seconds between retries
    # fixed: sleep `backoff` between attempts; exponential: backoff * 2^attempt
    # (attempt 0-based), capped at 60s per pause (Phase 33 — roadmap fase 2.1).
    backoffStrategy: str = Field(default="fixed", pattern="^(fixed|exponential)$")
    # Hard wall-clock cap for a single execution attempt, in milliseconds. 0
    # disables it. A timed-out attempt raises like any other failure, so it is
    # subject to retry/backoff and the onError policy below.
    timeoutMs: int = Field(default=0, ge=0, le=600000)
    continueOnFail: bool = False  # legacy alias of onError="continue"
    # After retries are exhausted: stop the run, continue on 'main' with
    # {error}, or route {error, input} through a dedicated 'error' handle.
    onError: str = Field(default="stop", pattern="^(stop|continue|branch)$")
    # Phase 34 (roadmap fase 3.2) — a frozen mock of this node's output, saved
    # with the workflow. Used instead of history by node tests, partial runs and
    # expression previews while developing; production runs ignore it entirely.
    pinnedOutput: Any | None = None
    # Phase 44 (roadmap fase 12.2) — dotted JSON paths into this node's output
    # (e.g. "body.card_number") masked as "***" before the output is persisted
    # (workflow_node_runs), streamed over SSE or exported. The real value stays
    # in the live run context so downstream nodes still resolve it in cleartext;
    # $secrets are never exposed to begin with (fase 1.3) and need no entry here.
    redact: list[str] = Field(default_factory=list)
    # Phase 46 (roadmap fase 14.1) — a label (e.g. "gpu", "internal-network",
    # "dmz"): this node executes on the first matching online remote runner
    # instead of the backend process. Empty/None = execute locally, as today.
    runOn: str | None = Field(default=None, max_length=64)
    # When no matching runner is online within the dispatch timeout (node
    # timeoutMs, or GRAPH_WORKFLOW_RUNNER_JOB_TIMEOUT when unset): "fail" raises
    # (subject to the node's own retry/onError like any other failure), "local"
    # falls back to executing the node in the backend process.
    runOnFallback: str = Field(default="fail", pattern="^(fail|local)$")


class GraphEdge(BaseModel):
    """A directed connection. ``sourceHandle`` distinguishes branch outputs
    (e.g. 'true'/'false' for an ``if`` node, or a case label for ``switch``)."""

    id: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    target: str = Field(..., min_length=1)
    sourceHandle: str = "main"
    targetHandle: str = "main"


class GraphNote(BaseModel):
    """Phase 40 (roadmap fase 8.2) — a canvas annotation: a sticky note or a
    named frame that groups nodes. Purely presentational: it is saved with the
    graph (versioned, exported) but the engine never reads it — ``_execute``
    only ever iterates ``nodes``/``edges``."""

    id: str = Field(..., min_length=1)
    kind: str = Field(default="note", pattern="^(note|frame)$")
    text: str = Field(default="", max_length=4000)  # markdown for notes, label for frames
    color: str = Field(default="", max_length=32)
    position: dict[str, float] = Field(default_factory=dict)  # {x, y}
    size: dict[str, float] = Field(default_factory=dict)      # {width, height}


class WorkflowGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    # Phase 40 (roadmap fase 8.2) — sticky notes and frames on the canvas. The
    # engine ignores them entirely; they only ever affect the editor rendering.
    notes: list[GraphNote] = Field(default_factory=list)


class GraphWorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    graph: WorkflowGraph = Field(default_factory=WorkflowGraph)
    # Phase 32 (roadmap fase 1) — per-workflow variables, exposed as $vars.<name>
    variables: dict[str, Any] = Field(default_factory=dict)
    # Phase 33 (roadmap fase 2.3) — runs beyond this many simultaneously active
    # (pending/running) go to `queued` and start when a slot frees. 0 = unlimited.
    max_concurrent_runs: int = Field(default=0, ge=0, le=100)
    # Phase 38 (roadmap fase 6.4) — optional JSON Schema contracts: `input_schema`
    # validates the payload a `subworkflow` node sends in, `output_schema` the
    # sink output it returns. A workflow with an input contract also appears in
    # the node catalog as a typed `workflow.<id>` node.
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    # Phase 39 (roadmap fase 7.2) — named environments: {name: {vars: {...},
    # secrets: {alias: secret_name}, version: <pinned graph version|absent>}}.
    # `vars` overlay the workflow $vars for runs in that environment, `secrets`
    # remap $secrets.<alias> to another stored secret, `version` pins the graph
    # ("promote to prod" sets it; absent = the current graph).
    environments: dict[str, Any] = Field(default_factory=dict)
    # Phase 41 (roadmap fase 9.1) — when true (and the workflow is active with an
    # input contract), it is published as a callable tool: to llm.agent, to other
    # workflows' tool.* nodes, to the product chat and to the MCP server. The tool
    # name/description/parameters derive from name/description/input_schema.
    expose_as_tool: bool = False
    # Phase 44 (roadmap fase 12.1) — LLM token / run caps for the calendar
    # month (None = unlimited). Crossing GRAPH_WORKFLOW_BUDGET_WARN_PCT of
    # either raises an in-app soft warning once per period; a cap fully
    # reached hard-stops new runs of this workflow for the rest of the period.
    token_budget_month: int | None = Field(default=None, ge=0)
    run_budget_month: int | None = Field(default=None, ge=0)
    # Phase 44 (roadmap fase 12.2) — run/node-run retention override in days
    # for this workflow (None = GRAPH_WORKFLOW_RUNS_RETENTION_DAYS default).
    runs_retention_days: int | None = Field(default=None, ge=0)
    # Phase 49 (roadmap fase 17) — scheduling/SLA/navigator settings.
    # 17.1 — blackout windows / holiday skips: {windows: [{start:"HH:MM",
    # end:"HH:MM", days:[0-6], tz}], skip_dates: ["YYYY-MM-DD"], on_conflict:
    # "skip"|"defer"}. 17.2 — SLA: {max_duration_s, missed_grace_s, channels}.
    # 17.5 — notify: {digest: {enabled, interval_s, channel}}.
    blackout: dict[str, Any] | None = None
    sla: dict[str, Any] | None = None
    notify: dict[str, Any] | None = None
    # 17.3 — navigator: folder path, free-form tags, archive flag.
    folder: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list)
    archived: bool = False


class GraphWorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    graph: WorkflowGraph | None = None
    active: bool | None = None
    variables: dict[str, Any] | None = None
    max_concurrent_runs: int | None = Field(default=None, ge=0, le=100)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    environments: dict[str, Any] | None = None
    expose_as_tool: bool | None = None
    token_budget_month: int | None = Field(default=None, ge=0)
    run_budget_month: int | None = Field(default=None, ge=0)
    runs_retention_days: int | None = Field(default=None, ge=0)
    # Phase 49 (roadmap fase 17) — None leaves the setting untouched; an empty
    # dict clears blackout/sla/notify. `tags`/`folder`/`archived` are set when
    # provided (None = untouched).
    blackout: dict[str, Any] | None = None
    sla: dict[str, Any] | None = None
    notify: dict[str, Any] | None = None
    folder: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = None
    archived: bool | None = None


class WorkflowGitSyncIn(BaseModel):
    """Body of PUT /{id}/git-sync (roadmap fase 13.3). ``repo_url`` empty/None
    disables sync. ``token_secret`` names a $secrets entry holding a Git access
    token (HTTPS); its VALUE never appears in this API."""

    repo_url: str | None = None
    branch: str = "main"
    token_secret: str | None = None
    subpath: str | None = None


class WorkflowGitSyncOut(BaseModel):
    repo_url: str | None = None
    branch: str = "main"
    token_secret: str | None = None
    subpath: str | None = None
    last_synced_at: int | None = None


class GitSyncPullOut(BaseModel):
    """Result of POST /{id}/git-sync/pull: pulled definitions that differ from
    the workflow's current draft chain become new versions, never overwriting
    the live graph."""

    imported_versions: list[int] = Field(default_factory=list)
    unchanged: bool = False


class GraphWorkflowOut(BaseModel):
    id: str
    profile_id: str
    name: str
    description: str
    graph: WorkflowGraph
    variables: dict[str, Any] = Field(default_factory=dict)
    max_concurrent_runs: int = 0
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    environments: dict[str, Any] = Field(default_factory=dict)
    expose_as_tool: bool = False
    token_budget_month: int | None = None
    run_budget_month: int | None = None
    runs_retention_days: int | None = None
    git_sync: WorkflowGitSyncOut | None = None
    # Phase 49 (roadmap fase 17) — scheduling/SLA/navigator settings.
    blackout: dict[str, Any] = Field(default_factory=dict)
    sla: dict[str, Any] = Field(default_factory=dict)
    notify: dict[str, Any] = Field(default_factory=dict)
    folder: str | None = None
    tags: list[str] = Field(default_factory=list)
    archived: bool = False
    active: bool
    version: int
    created_at: int
    updated_at: int
    triggers: list["WorkflowTriggerOut"] | None = None


class NodeRunOut(BaseModel):
    id: str
    run_id: str
    node_id: str
    node_type: str
    status: str
    input: Any | None = None
    output: Any | None = None
    error: str | None = None
    started_at: int | None = None
    finished_at: int | None = None


class GraphRunOut(BaseModel):
    id: str
    workflow_id: str
    workflow_name: str | None = None  # joined for the profile-wide run registry
    profile_id: str
    status: str
    trigger_type: str
    # Phase 39 (fase 7.2/7.1) — environment the run executed in and, for runs
    # created by retry/replay, the run they derive from.
    environment: str | None = None
    origin_run_id: str | None = None
    # Phase 40 (fase 8.3) — step-debug state {breakpoints, pending_node} for a
    # `paused` run; None for ordinary runs.
    debug: dict[str, Any] | None = None
    # Phase 48 (fase 16.4) — queue priority: higher runs are promoted first, FIFO
    # within the same priority. 0 = normal.
    priority: int = 0
    error: str | None = None
    created_at: int
    updated_at: int
    node_runs: list[NodeRunOut] | None = None


class RunCompareNode(BaseModel):
    """Phase 49 (roadmap fase 17.4) — one node row of a two-run comparison."""

    node_id: str
    node_type: str | None = None
    status_a: str | None = None
    status_b: str | None = None
    duration_ms_a: int | None = None
    duration_ms_b: int | None = None
    output_equal: bool = True
    output_a: Any | None = None
    output_b: Any | None = None


class RunCompareOut(BaseModel):
    """Phase 49 (roadmap fase 17.4) — side-by-side diff of two runs of the same
    workflow. ``first_divergent_node`` is the first node (in execution order) that
    differs in status or output — the answer to "why did it work yesterday?"."""

    workflow_id: str
    run_a: str
    run_b: str
    status_a: str
    status_b: str
    duration_ms_a: int | None = None
    duration_ms_b: int | None = None
    first_divergent_node: str | None = None
    nodes: list[RunCompareNode] = Field(default_factory=list)


class RunTriggerIn(BaseModel):
    """Body of POST /{id}/run — the trigger payload becomes ``$trigger``.

    ``start_node_id`` requests a **partial run**: execution starts at that node
    and upstream nodes are seeded from their latest persisted outputs instead
    of re-running (trigger_type is recorded as ``partial``).
    """

    payload: dict[str, Any] = Field(default_factory=dict)
    start_node_id: str | None = None
    # Phase 39 (fase 7.2) — run in a named environment (its vars/secrets
    # bindings apply; a pinned version replaces the current graph).
    environment: str | None = Field(default=None, max_length=64)
    # Phase 40 (fase 8.3) — start the run in step-debug mode: it is created in
    # status ``paused`` (no node has run yet) and advances only via
    # POST /runs/{id}/debug. ``breakpoints`` are node ids the run pauses before
    # when the debugger is told to "continue".
    debug: bool = False
    breakpoints: list[str] = Field(default_factory=list)
    # Phase 48 (fase 16.4) — queue priority for this run: when the workflow's
    # concurrency slots are full, higher-priority runs are promoted first.
    priority: int = Field(default=0, ge=-100, le=100)


class WorkflowStateIn(BaseModel):
    """Body of PUT /{id}/state/{key} (fase 16.1) — set/overwrite one state entry
    from the run panel. ``ttl_seconds`` > 0 gives the key an expiry."""

    value: Any = None
    ttl_seconds: int | None = Field(default=None, ge=0)


class WorkflowStateOut(BaseModel):
    """One persistent-state entry of a workflow (fase 16.1)."""

    key: str
    value: Any = None
    expires_at: int | None = None
    updated_at: int


class DebugCommandIn(BaseModel):
    """Body of POST /runs/{id}/debug (roadmap fase 8.3). ``step`` runs exactly
    the next node then pauses again; ``continue`` runs until the next breakpoint
    (or the end); ``stop`` cancels the paused run. ``breakpoints`` optionally
    replaces the run's breakpoint set before resuming; ``input`` optionally
    overrides the primary input fed to the very next node (edit-the-pin)."""

    command: str = Field(..., pattern="^(step|continue|stop)$")
    breakpoints: list[str] | None = None
    input: Any | None = None


class VersionDiffOut(BaseModel):
    """Result of GET /{id}/versions/{a}/diff/{b} (roadmap fase 8.1): what the
    graph gains, loses and changes from version ``a`` to version ``b``. Node ids
    are grouped so the editor can paint the target canvas (added = green,
    removed = red, changed = yellow); ``changed`` carries the per-node config
    diff for the inspector."""

    from_version: int
    to_version: int
    added_nodes: list[str] = Field(default_factory=list)
    removed_nodes: list[str] = Field(default_factory=list)
    changed_nodes: list[dict[str, Any]] = Field(default_factory=list)  # {id, before, after}
    unchanged_nodes: list[str] = Field(default_factory=list)
    added_edges: list[str] = Field(default_factory=list)
    removed_edges: list[str] = Field(default_factory=list)


class NodeTestIn(BaseModel):
    """Body of POST /{id}/nodes/{node_id}/test (roadmap fase 3.1).

    ``node`` optionally carries the node's *unsaved* editor state so a test
    reflects what is on the canvas without forcing a save; its id must match
    the path. ``input`` optionally mocks the node's primary input ($json) —
    when absent the input comes from the pinned/latest output of the first
    incoming edge's source, falling back to the latest trigger payload.
    """

    input: Any | None = None
    node: GraphNode | None = None


class ExpressionPreviewIn(BaseModel):
    """Body of POST /{id}/preview-expression — evaluated read-only against the
    workflow's latest run data (node outputs + trigger payload)."""

    expression: str = Field(..., max_length=4000)


class WorkflowTriggerCreate(BaseModel):
    # `error` (fase 2.5): fires when another workflow's run fails; its config may
    # carry {"workflow_id": "<id>"} to watch a single workflow ("", "*" = any).
    # `success` (fase 6.1) mirrors it for successful completions. `file.watch`
    # (fase 6.2) polls a workspace-storage subfolder ({path, pattern, events});
    # `email.inbound` (fase 6.2) polls an IMAP inbox ({host, username,
    # password_secret, from, subject, …}). `chat` (fase 9.3) turns the workflow
    # into a chatbot driven by POST /{id}/chat.
    type: str = Field(..., pattern=r"^(manual|schedule|webhook|event|error|success|file\.watch|email\.inbound|chat|queue\.consume|rss\.read)$")
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class WorkflowTriggerOut(BaseModel):
    id: str
    workflow_id: str
    type: str
    config: dict[str, Any]
    token: str | None = None
    next_run_at: int | None = None
    enabled: bool
    created_at: int
    fail_count: int = 0
    last_error: str | None = None


class WorkflowScheduleOut(BaseModel):
    """One row of the cross-workflow schedules overview (Phase 30.e)."""

    workflow_id: str
    workflow_name: str
    workflow_active: bool
    trigger_id: str
    trigger_type: str
    config: dict[str, Any]
    next_run_at: int | None = None
    enabled: bool
    fail_count: int = 0
    last_error: str | None = None
    last_run_status: str | None = None
    last_run_at: int | None = None


class WorkflowSecretIn(BaseModel):
    """Body of PUT /secrets — upserts one profile-scoped secret. The value is
    Fernet-encrypted at rest and only ever readable by the engine ($secrets)."""

    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str = Field(..., min_length=1, max_length=4000)


class WorkflowSecretOut(BaseModel):
    """A secret as listed by the API — the value is intentionally absent."""

    name: str
    created_at: int
    updated_at: int


class WorkflowStatsOut(BaseModel):
    """Aggregated per-workflow metrics (Phase 36 — roadmap fase 5.1): run counts
    by outcome, success rate over terminal runs, average duration and the LLM
    token totals reported by llm.* node runs (`_usage`)."""

    workflow_id: str
    workflow_name: str
    active: bool = False
    runs: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    success_rate: float | None = None  # completed / (completed + failed), None with no terminal runs
    avg_duration_s: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    last_run_at: int | None = None


class WorkflowImportIn(BaseModel):
    """Body of POST /import (Phase 36 — roadmap fase 5.2): the portable snapshot
    produced by GET /{id}/export. Extra export-only fields (kind, schema_version,
    exported_at…) are accepted and ignored."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    graph: WorkflowGraph
    variables: dict[str, Any] = Field(default_factory=dict)
    max_concurrent_runs: int = Field(default=0, ge=0, le=100)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    environments: dict[str, Any] = Field(default_factory=dict)
    expose_as_tool: bool = False


class WorkflowImportOut(BaseModel):
    workflow: "GraphWorkflowOut"
    # Non-blocking issues found during validation: unknown node types, edges to
    # missing nodes (dropped), $secrets references not present in this profile.
    warnings: list[str] = Field(default_factory=list)


class WorkflowGenerateIn(BaseModel):
    """Body of POST /generate (Phase 36 — roadmap fase 5.3): natural-language
    description → a validated draft graph (NOT saved; the editor opens it).
    ``model`` overrides the default; ``failover_chain`` names a Settings → Models
    chain to fall back through on call failure (same semantics as llm.* nodes)."""

    prompt: str = Field(..., min_length=3, max_length=4000)
    model: str | None = None
    failover_chain: str | None = None


class WorkflowGenerateOut(BaseModel):
    name: str
    description: str = ""
    graph: WorkflowGraph
    warnings: list[str] = Field(default_factory=list)
    model: str | None = None


class SharedWorkflowOut(BaseModel):
    """One workflow shared into a workspace (Phase 36 — roadmap fase 5.2)."""

    workflow_id: str
    name: str
    description: str = ""
    version: int = 1
    node_count: int = 0
    shared_by: str
    shared_at: int
    updated_at: int
    # Phase 39 (fase 7.3) — what members may do with the share:
    # viewer (inspect/import) | editor (also run) | approver (also decide approvals).
    role: str = "viewer"


class WorkflowApprovalOut(BaseModel):
    """One human-in-the-loop "waiting request": approval (Phase 35 — roadmap
    fase 4.4), form input or event correlation (Phase 42 — roadmap fase 10).
    Created by a ``human.approval``/``human.input``/``wait.event`` node; the
    run sits in status ``waiting`` until it is decided or ``timeout_at`` passes."""

    id: str
    run_id: str
    node_id: str
    workflow_id: str
    workflow_name: str | None = None  # joined for the pending-approvals list
    profile_id: str
    kind: str = "approval"  # approval|input|event (fase 10)
    title: str = ""
    message: str = ""
    status: str = "pending"
    timeout_at: int | None = None
    comment: str | None = None
    decided_by: str | None = None
    form_schema: dict[str, Any] | None = None  # human.input: JSON Schema of the requested form
    data: Any | None = None  # human.input: submitted form data; wait.event: delivered payload
    correlation_id: str | None = None  # wait.event
    created_at: int
    decided_at: int | None = None


class ApprovalDecisionIn(BaseModel):
    """Body of POST /approvals/{id}/decision."""

    approved: bool
    comment: str | None = Field(default=None, max_length=2000)


class HumanInputSubmitIn(BaseModel):
    """Body of POST /approvals/{id}/submit — fase 10.1, ``human.input`` form."""

    data: dict[str, Any]
    comment: str | None = Field(default=None, max_length=2000)


class EventDeliverIn(BaseModel):
    """Body of POST /events/{correlation_id} — fase 10.2, ``wait.event``."""

    payload: dict[str, Any] = Field(default_factory=dict)


class NodeTypeInfo(BaseModel):
    """Catalog entry for the node palette in the visual editor."""

    type: str
    category: str  # trigger|action|logic|data|ai
    label: str
    description: str
    inputs: int = 1
    outputs: list[str] = Field(default_factory=lambda: ["main"])
    params_schema: list[dict[str, Any]] = Field(default_factory=list)
    # Node-level field defaults applied by the editor when the node is dropped
    # on the canvas (fase 2.1 — e.g. retry/backoff presets for http.request/llm.*).
    defaults: dict[str, Any] = Field(default_factory=dict)


class GraphWorkflowExport(BaseModel):
    """Portable, self-contained snapshot of a workflow (Phase 30.f) — the body
    returned by ``GET /{id}/export`` and accepted by ``POST /import``. Triggers,
    runs and ids are intentionally omitted so the file is environment-agnostic."""

    schema_version: int = 1
    name: str
    description: str = ""
    graph: WorkflowGraph
    variables: dict[str, Any] = Field(default_factory=dict)  # $vars travel with the file; $secrets never do
    max_concurrent_runs: int = 0
    input_schema: dict[str, Any] | None = None  # fase 6.4 — contracts travel with the file
    output_schema: dict[str, Any] | None = None
    expose_as_tool: bool = False  # fase 9.1 — the tool-exposure flag travels too
    exported_at: int | None = None


class WorkflowNodeStatsOut(BaseModel):
    """Per-node aggregates over the workflow's run history (Phase 39 — roadmap
    fase 7.4): execution counts by outcome, error rate, p50/p95 duration and
    the LLM tokens reported by the node's runs. Feeds the Health tab."""

    node_id: str
    node_type: str
    executions: int = 0
    ok: int = 0
    error: int = 0
    skipped: int = 0
    error_rate: float | None = None  # error / (ok + error), None with no terminal executions
    avg_duration_s: float | None = None
    p50_duration_s: float | None = None
    p95_duration_s: float | None = None
    tokens_total: int = 0
    last_executed_at: int | None = None


class WorkflowNodeVariantStatsOut(BaseModel):
    """Per-variant aggregates for one node (Phase 50 — roadmap fase 18.2): how
    each A/B prompt/model variant performed over the run history, so a winner can
    be declared. ``avg_score`` comes from ``llm.judge`` outputs; ``pass_rate`` is
    the share of judged runs that passed. ``winner`` flags the leading variant
    (highest avg_score, else highest ok-rate). Powers the variant breakdown UI."""

    variant: str
    executions: int = 0
    ok: int = 0
    error: int = 0
    ok_rate: float | None = None       # ok / (ok + error)
    avg_score: float | None = None     # mean judge score, None when the node isn't a judge
    pass_rate: float | None = None     # share of judged runs with passed == true
    avg_tokens: float | None = None
    winner: bool = False


class WorkflowAuditEntryOut(BaseModel):
    """One audit-log entry scoped to a workflow (Phase 39 — roadmap fase 7.3):
    who created/modified/activated/executed/approved what and when."""

    id: str
    user_id: str | None = None
    action: str
    resource: str | None = None
    detail: str | None = None
    created_at: int


class EnvironmentPromoteIn(BaseModel):
    """Body of POST /{id}/environments/{env}/promote (fase 7.2). ``version``
    pins that graph version for the environment; omitted = the current one."""

    version: int | None = Field(default=None, ge=1)


class WorkflowChatIn(BaseModel):
    """Body of POST /{id}/chat (Phase 41 — roadmap fase 9.3): one conversation
    turn against a ``chat``-triggered workflow. ``session_id`` correlates turns
    (a fresh one is minted when omitted); ``message`` is the user's text. The
    workflow runs with $trigger = {session_id, message, history} and its
    terminal ``chat.reply`` node's text comes back as ``reply``."""

    message: str = Field(..., min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, max_length=128)


class WorkflowChatOut(BaseModel):
    session_id: str
    reply: str
    run_id: str


class OpenApiImportIn(BaseModel):
    """Body of POST /openapi/import (Phase 41 — roadmap fase 9.4): an OpenAPI
    spec, given inline (``spec``) or by ``url``. Each operation becomes a
    preconfigured ``http.request`` node draft returned in ``nodes`` (NOT saved;
    the editor drops them onto the canvas)."""

    url: str | None = Field(default=None, max_length=2000)
    spec: dict[str, Any] | None = None
    # Optional filter: only import operations whose path starts with this prefix.
    path_prefix: str = Field(default="", max_length=200)


class OpenApiOperationOut(BaseModel):
    """One generated node + the operation metadata that produced it."""

    operation_id: str
    method: str
    path: str
    summary: str = ""
    node: GraphNode


class OpenApiImportOut(BaseModel):
    api_title: str = ""
    base_url: str = ""
    operations: list[OpenApiOperationOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExposedWorkflowToolOut(BaseModel):
    """One workflow published as a tool (Phase 41 — roadmap fase 9.1/9.2): the
    OpenAI/MCP tool name, description and JSON-Schema parameters derived from its
    input contract. Feeds the tools listing and the MCP server's tools/list."""

    tool_name: str
    workflow_id: str
    workflow_name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class McpRpcIn(BaseModel):
    """A single JSON-RPC 2.0 request to the product's workflow MCP server
    (Phase 41 — roadmap fase 9.2). Supported methods: ``initialize``,
    ``tools/list``, ``tools/call``, ``ping``."""

    jsonrpc: str = "2.0"
    id: Any | None = None
    method: str = Field(..., min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class TestAssertion(BaseModel):
    """One check within a workflow test case (roadmap fase 11.1): asserts on the
    output of a chosen node. ``path`` is a dot path into the output for
    ``json_path`` (e.g. ``data.items.0.id``); ``expected`` is a JSON Schema for
    ``schema``, the substring/member for ``contains``, or the exact value for
    ``equals``."""

    node_id: str = Field(..., min_length=1)
    type: str = Field(..., pattern="^(equals|contains|json_path|schema)$")
    path: str | None = Field(default=None, max_length=500)
    expected: Any = None


class WorkflowTestCaseIn(BaseModel):
    """Body of POST/PUT .../test-cases — a saved fixture + assertions."""

    name: str = Field(..., min_length=1, max_length=200)
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    assertions: list[TestAssertion] = Field(default_factory=list)


class WorkflowTestCaseOut(BaseModel):
    id: str
    workflow_id: str
    name: str
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    assertions: list[TestAssertion] = Field(default_factory=list)
    created_at: int
    updated_at: int


class TestAssertionResultOut(BaseModel):
    node_id: str
    type: str
    expected: Any = None
    actual: Any = None
    passed: bool
    message: str = ""


class TestCaseResultOut(BaseModel):
    case_id: str
    name: str
    passed: bool
    run_id: str | None = None
    error: str | None = None
    assertions: list[TestAssertionResultOut] = Field(default_factory=list)


class TestSuiteRunOut(BaseModel):
    """Result of POST /{id}/test-cases/run (roadmap fase 11.1)."""

    workflow_id: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    results: list[TestCaseResultOut] = Field(default_factory=list)


class JsonPatchOpOut(BaseModel):
    """One entry of the fase-13.2 review diff — not full RFC 6902, just
    add/remove/replace on the failed node's top-level params keys."""

    op: str
    path: str
    value: Any | None = None


class WorkflowExplainOut(BaseModel):
    """Result of POST /runs/{run_id}/explain (roadmap fase 13.2). ``patch``/
    ``proposed_params`` are None when the model had no concrete fix to offer;
    never applied automatically — the editor shows them for accept/discard."""

    node_id: str
    explanation: str
    proposed_params: dict[str, Any] | None = None
    patch: list[JsonPatchOpOut] | None = None
    model: str | None = None


class WorkflowDryRunIn(BaseModel):
    """Body of POST /{id}/dry-run (roadmap fase 11.2)."""

    payload: dict[str, Any] = Field(default_factory=dict)


class DryRunEffectOut(BaseModel):
    """One node that a real run would have had an external effect on, mocked
    during the dry-run. ``source`` is ``pin`` when the node's pinned output
    (fase 3.2) was used, ``placeholder`` when a typed stand-in was generated."""

    node_id: str
    node_type: str
    source: str = "placeholder"


class WorkflowDryRunOut(BaseModel):
    """Result of POST /{id}/dry-run: the path taken, every node's simulated
    output and the external effects a real run would have performed."""

    run_id: str
    status: str
    path: list[str] = Field(default_factory=list)
    node_outputs: dict[str, Any] = Field(default_factory=dict)
    external_effects: list[DryRunEffectOut] = Field(default_factory=list)
    error: str | None = None


class WorkflowCostEstimateOut(BaseModel):
    """Result of GET /{id}/cost-estimate (roadmap fase 11.3): a static
    token/month projection from historical LLM usage (fase 5.1) and the
    workflow's active schedule frequency. Tokens only — no invented pricing."""

    workflow_id: str
    llm_node_count: int = 0
    avg_tokens_per_run: float | None = None
    runs_per_month_est: float | None = None
    tokens_per_month_est: float | None = None
    basis: str = ""


# ── Phase 44 — budgets and quotas (roadmap fase 12.1) ───────────────────────

class WorkflowBudgetStatusOut(BaseModel):
    """Result of GET /{id}/budget: the workflow's own caps plus its usage over
    the current calendar-month period, and — since a workflow is also gated by
    the profile-wide ("workspace") cap — that cap's own usage/status too."""

    workflow_id: str
    period: str  # "YYYY-MM", UTC
    token_budget_month: int | None = None
    run_budget_month: int | None = None
    tokens_used: int = 0
    runs_used: int = 0
    exceeded: bool = False
    profile_token_budget_month: int | None = None
    profile_run_budget_month: int | None = None
    profile_tokens_used: int = 0
    profile_runs_used: int = 0
    profile_exceeded: bool = False


class ProfileBudgetIn(BaseModel):
    token_budget_month: int | None = Field(default=None, ge=0)
    run_budget_month: int | None = Field(default=None, ge=0)


class ProfileBudgetOut(BaseModel):
    profile_id: str
    token_budget_month: int | None = None
    run_budget_month: int | None = None


class GraphWorkflowExample(BaseModel):
    """Phase 29 — a curated, importable graph workflow (one-click import)."""

    id: str
    title: str
    description: str
    category: str
    node_types: list[str]
    graph: WorkflowGraph


# ── Phase 46 — remote execution and scalability (roadmap fase 14) ──────────

class RunnerRegisterIn(BaseModel):
    """Body of POST /runners (roadmap fase 14.1): a user provisions a new
    remote runner slot and gets back a raw token, shown once — the runner
    process is started with it (e.g. SIBYL_RUNNER_TOKEN) and presents it as
    ``X-Runner-Token`` on every heartbeat/poll/result call thereafter."""

    name: str = Field(..., min_length=1, max_length=120)
    labels: list[str] = Field(default_factory=list)
    # Node types this runner is allowed to execute (e.g. a DMZ runner gets
    # only ["http.request"]). Empty = no restriction beyond the label match.
    allowed_node_types: list[str] = Field(default_factory=list)


class RunnerRegisterOut(BaseModel):
    id: str
    token: str  # only ever returned here — not retrievable again


class RunnerHeartbeatIn(BaseModel):
    """Body of POST /wf/runners/heartbeat — sent by the runner process itself,
    authenticated by its ``X-Runner-Token`` header (not a user session)."""

    version: str | None = Field(default=None, max_length=64)
    labels: list[str] | None = None


class RunnerOut(BaseModel):
    id: str
    name: str
    labels: list[str] = Field(default_factory=list)
    allowed_node_types: list[str] = Field(default_factory=list)
    version: str | None = None
    status: str = "offline"  # online|offline, derived from last_heartbeat_at
    last_heartbeat_at: int | None = None
    created_at: int


class RunnerJobOut(BaseModel):
    """Body handed to a runner by GET /wf/runners/jobs/next — the fase 3.1
    test_node() contract: type + already-resolved params (any $secrets are
    inlined literal values, never the vault) + input."""

    job_id: str
    node_id: str
    node_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    input: Any | None = None


class RunnerJobResultIn(BaseModel):
    """Body of POST /wf/runners/jobs/{job_id}/result — mirrors test_node()'s
    return shape. ``logs`` are free-form lines surfaced in the node run for
    troubleshooting; never persisted beyond the job row."""

    ok: bool
    output: Any | None = None
    handles: list[str] = Field(default_factory=lambda: ["main"])
    error: str | None = None
    logs: list[str] = Field(default_factory=list)


GraphWorkflowOut.model_rebuild()
