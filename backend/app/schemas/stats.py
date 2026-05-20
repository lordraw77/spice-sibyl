from pydantic import BaseModel


class TelegramStats(BaseModel):
    enabled: bool
    active_chats: int = 0
    messages_received: int = 0
    messages_sent: int = 0
    errors: int = 0


class GlobalStats(BaseModel):
    total_conversations: int
    total_messages: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost: float


class ProfileSummary(BaseModel):
    profile_id: str
    profile_name: str
    total_conversations: int
    total_messages: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost: float


class ProfileSlice(BaseModel):
    """Per-profile breakdown row nested inside a provider or model entry."""
    profile_id: str
    profile_name: str
    message_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost: float


class ProviderStats(BaseModel):
    provider: str | None
    message_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost: float
    avg_latency_ms: float | None
    avg_tokens_per_second: float | None
    by_profile: list[ProfileSlice] = []


class ModelStats(BaseModel):
    model: str | None
    provider: str | None
    message_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost: float
    avg_latency_ms: float | None
    avg_tokens_per_second: float | None
    by_profile: list[ProfileSlice] = []


class UsageStats(BaseModel):
    global_stats: GlobalStats
    by_profile: list[ProfileSummary]
    by_provider: list[ProviderStats]
    by_model: list[ModelStats]
    telegram: TelegramStats | None = None
