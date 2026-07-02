"""
Phase 18 — persistent multi-step workflows (agent runs) schemas.

An agent run is a durable server-side tool loop: the model is given a goal and
the full tool registry and iterates (assistant → tool calls → tool results)
until it produces a final answer or hits ``max_steps``. Every step is persisted
(``agent_run_steps``) and the serialized message history is checkpointed after
each iteration, so a run can be paused, resumed — even across restarts — and
inspected step by step from the UI.
"""

from pydantic import BaseModel, Field

# pending → running → (paused ⇄ running) → completed | failed | cancelled
RUN_STATUSES = ("pending", "running", "paused", "completed", "failed", "cancelled")


class AgentRunCreate(BaseModel):
    """Create-and-start payload for POST /workflows."""

    goal: str = Field(..., min_length=1, max_length=8000)
    model: str = Field(..., min_length=1)
    max_steps: int | None = Field(default=None, ge=1)
    system_prompt: str | None = Field(default=None, max_length=8000)


class AgentRunStep(BaseModel):
    """One persisted step of a run."""

    id: str
    run_id: str
    step_index: int
    kind: str            # 'assistant' | 'tool_call' | 'tool_result' | 'final' | 'error' | 'note'
    name: str | None = None   # tool name for tool_call / tool_result
    content: str = ""
    created_at: int


class AgentRunOut(BaseModel):
    """A workflow run; ``steps`` populated only on the detail endpoint."""

    id: str
    profile_id: str
    goal: str
    model: str
    status: str
    max_steps: int
    current_step: int
    result: str | None = None
    error: str | None = None
    created_at: int
    updated_at: int
    steps: list[AgentRunStep] | None = None
