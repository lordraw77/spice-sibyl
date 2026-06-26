from pydantic import BaseModel


class PromptTemplateCreate(BaseModel):
    name: str
    content: str
    profile_id: str | None = None


class PromptTemplateUpdate(BaseModel):
    name: str | None = None
    content: str | None = None


class PromptTemplate(BaseModel):
    id: str
    profile_id: str
    name: str
    content: str
    created_at: int
    updated_at: int
