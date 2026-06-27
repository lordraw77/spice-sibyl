from pydantic import BaseModel


class ProfileCreate(BaseModel):
    name: str


class Profile(BaseModel):
    id: str
    name: str
    created_at: int
    user_id: str | None = None
