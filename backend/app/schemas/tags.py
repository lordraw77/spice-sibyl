from pydantic import BaseModel


class TagCreate(BaseModel):
    name: str
    color: str = "#d6b279"
    profile_id: str | None = None


class TagUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class Tag(BaseModel):
    id: str
    profile_id: str
    name: str
    color: str
    created_at: int


class SetConversationTags(BaseModel):
    tag_ids: list[str]
