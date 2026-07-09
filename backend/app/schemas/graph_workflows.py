"""
Phase 29 — visual node-graph workflow engine schemas.

A *graph workflow* is a deterministic DAG of typed nodes: a trigger feeds one or
more downstream nodes connected by edges. ``graph_json`` ({nodes, edges}) is the
source of truth. This coexists with the Phase 18 agent runs (``schemas/workflows.py``);
the agent loop is exposed here as the ``llm.agent`` node type.
"""

from typing import Any

from pydantic import BaseModel, Field

# pending → running → completed | failed | cancelled
GRAPH_RUN_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
NODE_RUN_STATUSES = ("pending", "running", "ok", "error", "skipped")
TRIGGER_TYPES = ("manual", "schedule", "webhook", "event")


class GraphNode(BaseModel):
    """One node in the graph. ``type`` selects the executor; ``params`` are
    resolved through the expression resolver immediately before execution."""

    id: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)  # manual|schedule|webhook|event|tool.<name>|set|if|switch|merge|filter|code|llm.completion|llm.agent
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=dict)  # {x, y} for the canvas
    retry: int = Field(default=0, ge=0, le=10)
    backoff: float = Field(default=0.0, ge=0.0, le=60.0)  # seconds between retries
    continueOnFail: bool = False


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


class GraphWorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    graph: WorkflowGraph | None = None
    active: bool | None = None


class GraphWorkflowOut(BaseModel):
    id: str
    profile_id: str
    name: str
    description: str
    graph: WorkflowGraph
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
    profile_id: str
    status: str
    trigger_type: str
    error: str | None = None
    created_at: int
    updated_at: int
    node_runs: list[NodeRunOut] | None = None


class RunTriggerIn(BaseModel):
    """Body of POST /{id}/run — the trigger payload becomes ``$trigger``."""

    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowTriggerCreate(BaseModel):
    type: str = Field(..., pattern="^(manual|schedule|webhook|event)$")
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


class NodeTypeInfo(BaseModel):
    """Catalog entry for the node palette in the visual editor."""

    type: str
    category: str  # trigger|action|logic|data|ai
    label: str
    description: str
    inputs: int = 1
    outputs: list[str] = Field(default_factory=lambda: ["main"])
    params_schema: list[dict[str, Any]] = Field(default_factory=list)


class GraphWorkflowExample(BaseModel):
    """Phase 29 — a curated, importable graph workflow (one-click import)."""

    id: str
    title: str
    description: str
    category: str
    node_types: list[str]
    graph: WorkflowGraph


GraphWorkflowOut.model_rebuild()
