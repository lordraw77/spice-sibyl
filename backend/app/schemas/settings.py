from pydantic import BaseModel, Field


class SettingsBlob(BaseModel):
    # Opaque roaming-preferences blob; the frontend owns its shape (Phase 23).
    data: dict = Field(default_factory=dict)
