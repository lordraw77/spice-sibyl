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
    created_at: int
    updated_at: int
    tags: list[Tag] = []


class Conversation(ConversationSummary):
    messages: list[ChatMessage]


class AppendMessagesRequest(BaseModel):
    messages: list[ChatMessage]


class SearchResult(BaseModel):
    id: str
    title: str
    model: str
    updated_at: int
    snippet: str
