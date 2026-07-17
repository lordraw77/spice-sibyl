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
GRAPH_RUN_STATUSES = ("queued", "pending", "running", "waiting", "completed", "failed", "cancelled")
APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "cancelled")
NODE_RUN_STATUSES = ("pending", "running", "ok", "error", "skipped")
# `error` (Phase 33 — roadmap fase 2.5) fires the workflow when another run fails.
TRIGGER_TYPES = ("manual", "schedule", "webhook", "event", "error")


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


class GraphEdge(BaseModel):
    """A directed connection. ``sourceHandle`` distinguishes branch outputs
    (e.g. 'true'/'false' for an ``if`` node, or a case label for ``switch``)."""

    id: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    target: str = Field(..., min_length=1)
    sourceHandle: str = "main"
    targetHandle: str = "main"


class WorkflowGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class GraphWorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    graph: WorkflowGraph = Field(default_factory=WorkflowGraph)
    # Phase 32 (roadmap fase 1) — per-workflow variables, exposed as $vars.<name>
    variables: dict[str, Any] = Field(default_factory=dict)
    # Phase 33 (roadmap fase 2.3) — runs beyond this many simultaneously active
    # (pending/running) go to `queued` and start when a slot frees. 0 = unlimited.
    max_concurrent_runs: int = Field(default=0, ge=0, le=100)


class GraphWorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    graph: WorkflowGraph | None = None
    active: bool | None = None
    variables: dict[str, Any] | None = None
    max_concurrent_runs: int | None = Field(default=None, ge=0, le=100)


class GraphWorkflowOut(BaseModel):
    id: str
    profile_id: str
    name: str
    description: str
    graph: WorkflowGraph
    variables: dict[str, Any] = Field(default_factory=dict)
    max_concurrent_runs: int = 0
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
    error: str | None = None
    created_at: int
    updated_at: int
    node_runs: list[NodeRunOut] | None = None


class RunTriggerIn(BaseModel):
    """Body of POST /{id}/run — the trigger payload becomes ``$trigger``.

    ``start_node_id`` requests a **partial run**: execution starts at that node
    and upstream nodes are seeded from their latest persisted outputs instead
    of re-running (trigger_type is recorded as ``partial``).
    """

    payload: dict[str, Any] = Field(default_factory=dict)
    start_node_id: str | None = None


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
    type: str = Field(..., pattern="^(manual|schedule|webhook|event|error)$")
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


class WorkflowApprovalOut(BaseModel):
    """One human-in-the-loop approval request (Phase 35 — roadmap fase 4.4).
    Created by a ``human.approval`` node; the run sits in status ``waiting``
    until it is decided or ``timeout_at`` passes."""

    id: str
    run_id: str
    node_id: str
    workflow_id: str
    workflow_name: str | None = None  # joined for the pending-approvals list
    profile_id: str
    title: str = ""
    message: str = ""
    status: str = "pending"
    timeout_at: int | None = None
    comment: str | None = None
    decided_by: str | None = None
    created_at: int
    decided_at: int | None = None


class ApprovalDecisionIn(BaseModel):
    """Body of POST /approvals/{id}/decision."""

    approved: bool
    comment: str | None = Field(default=None, max_length=2000)


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
    exported_at: int | None = None


class GraphWorkflowExample(BaseModel):
    """Phase 29 — a curated, importable graph workflow (one-click import)."""

    id: str
    title: str
    description: str
    category: str
    node_types: list[str]
    graph: WorkflowGraph


GraphWorkflowOut.model_rebuild()
