from typing import Any, Literal
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]
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
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = 0.7
    max_tokens: int | None = Field(default=1024, alias="max_tokens")


class ChatCompletionChoice(BaseModel):
    index: int
    finish_reason: str | None
    message: ChatMessage


class ChatUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatMetrics(BaseModel):
    latency_ms: int | None = None
    first_token_ms: int | None = None
    tokens_per_second: float | None = None
    provider: str | None = None
    estimated_cost: float | None = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatUsage | None = None
    metrics: ChatMetrics | None = None
