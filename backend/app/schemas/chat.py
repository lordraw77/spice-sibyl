"""
Pydantic schemas for the OpenAI-compatible chat completions API.

ChatMessage is intentionally extended with extra telemetry fields (latency,
token counts, cost) so the frontend can display per-message performance stats
without a separate metrics endpoint.
"""

from typing import Any, Literal
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A single turn in a conversation, optionally carrying telemetry metadata."""

    role: Literal["system", "user", "assistant", "tool"]
    # Content is a plain string for text models; a list of dicts for multimodal payloads.
    content: str | list[dict[str, Any]]

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


class ChatCompletionRequest(BaseModel):
    """Incoming request body for POST /chat/completions."""

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = 0.7
    max_tokens: int | None = Field(default=1024, alias="max_tokens")


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
