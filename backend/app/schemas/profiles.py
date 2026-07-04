from pydantic import BaseModel


class ProfileCreate(BaseModel):
    name: str


class ProfileUpdate(BaseModel):
    # Phase 22: UI locale (one of the SUPPORTED_LOCALES codes, or None to unset)
    locale: str | None = None


class Profile(BaseModel):
    id: str
    name: str
    created_at: int
    user_id: str | None = None
    # Phase 22: persisted UI language; None = follow the browser language
    locale: str | None = None
