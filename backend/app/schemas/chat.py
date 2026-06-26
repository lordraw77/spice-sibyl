"""
Pydantic schemas for the OpenAI-compatible chat completions API.

ChatMessage is intentionally extended with extra telemetry fields (latency,
token counts, cost) so the frontend can display per-message performance stats
without a separate metrics endpoint.
"""

from typing import Any, Literal
from pydantic import BaseModel, Field


class ToolCallFunction(BaseModel):
    name: str
    arguments: str  # JSON-encoded string, as per OpenAI spec


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: ToolCallFunction


class ToolFunctionParams(BaseModel):
    type: str = "object"
    properties: dict[str, Any] = {}
    required: list[str] = []


class ToolFunction(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = {}


class ToolDefinition(BaseModel):
    type: str = "function"
    function: ToolFunction


class ChatMessage(BaseModel):
    """A single turn in a conversation, optionally carrying telemetry metadata."""

    role: Literal["system", "user", "assistant", "tool"]
    # Content is None when the assistant message carries only tool_calls
    content: str | list[dict[str, Any]] | None = None

    # Tool calling fields
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # present on role="tool" result messages
    name: str | None = None          # optional tool name on result messages

    # Telemetry fields — populated on assistant messages returned by providers
    model: str | None = None
    provider: str | None = None
    latency_ms: int | None = None
    first_token_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    tokens_per_second: float | None = None
    finish_reason: str | None = None
    estimated_cost: float | None = None
    created_at: int | None = None
    capabilities: list[str] | None = None
    free: bool | None = None

    # Persistence fields
    id: str | None = None
    pinned: bool | None = None
    parent_id: str | None = None
    branch_index: int | None = None


class ChatCompletionRequest(BaseModel):
    """Incoming request body for POST /chat/completions."""

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = 0.7
    max_tokens: int | None = Field(default=1024, alias="max_tokens")
    tools: list[ToolDefinition] | None = None
    tool_choice: str | None = None


class ChatCompletionChoice(BaseModel):
    """A single candidate completion returned by the model."""

    index: int
    finish_reason: str | None
    message: ChatMessage


class ChatUsage(BaseModel):
    """Token consumption reported by the provider."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatMetrics(BaseModel):
    """Gateway-level performance metrics attached to every completion response."""

    latency_ms: int | None = None
    first_token_ms: int | None = None
    tokens_per_second: float | None = None
    provider: str | None = None
    estimated_cost: float | None = None


class ChatCompletionResponse(BaseModel):
    """Full response envelope — mirrors the OpenAI chat.completion object."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatUsage | None = None
    metrics: ChatMetrics | None = None
