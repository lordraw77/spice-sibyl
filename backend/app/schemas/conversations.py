from pydantic import BaseModel

from app.schemas.chat import ChatMessage
from app.schemas.tags import Tag


class ConversationCreate(BaseModel):
    title: str
    model: str
    profile_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str


class ConversationSummary(BaseModel):
    id: str
    title: str
    model: str
    # Phase 23.a: channel of origin — "web" (default) or "telegram"
    channel: str = "web"
    created_at: int
    updated_at: int
    tags: list[Tag] = []


class Conversation(ConversationSummary):
    messages: list[ChatMessage]


class AppendMessagesRequest(BaseModel):
    messages: list[ChatMessage]
    # Phase 19: false = incognito exchange — skip memory extraction for this batch
    memory: bool = True


class SearchResult(BaseModel):
    id: str
    title: str
    model: str
    updated_at: int
    snippet: str
