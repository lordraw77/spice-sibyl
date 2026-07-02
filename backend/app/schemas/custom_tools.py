"""
Phase 18 — user-defined custom tools schemas.

A custom tool is an HTTP-backed function the model can call from the chat tool
loop, registered from the UI without code changes. Per profile. Example::

    {
      "name": "get_weather",
      "description": "Current weather for a city",
      "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                     "required": ["city"]},
      "endpoint": {"url": "https://api.example.com/weather", "method": "POST",
                   "auth": {"type": "bearer", "token": "..."}}
    }

Arguments are sent as the JSON body (POST/PUT/PATCH) or query params (GET);
the response body (JSON or text) is returned to the model as the tool result.
"""

from pydantic import BaseModel, Field, field_validator

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


class CustomToolAuth(BaseModel):
    """Authentication attached to the outgoing HTTP call.

    * ``none``   — no auth (default).
    * ``bearer`` — `Authorization: Bearer <token>`.
    * ``header`` — arbitrary header `<name>: <value>` (e.g. X-Api-Key).
    """

    type: str = "none"  # 'none' | 'bearer' | 'header'
    token: str | None = None       # bearer
    name: str | None = None        # header
    value: str | None = None       # header

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        v = (v or "none").lower()
        if v not in ("none", "bearer", "header"):
            raise ValueError("auth type must be 'none', 'bearer' or 'header'")
        return v


class CustomToolEndpoint(BaseModel):
    """Where and how the tool call is delivered."""

    url: str = Field(..., min_length=1)
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    auth: CustomToolAuth = Field(default_factory=CustomToolAuth)
    timeout: float = Field(default=15.0, gt=0, le=120)

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return v

    @field_validator("method")
    @classmethod
    def _valid_method(cls, v: str) -> str:
        v = (v or "POST").upper()
        if v not in _METHODS:
            raise ValueError(f"method must be one of {', '.join(_METHODS)}")
        return v


class CustomToolIn(BaseModel):
    """Create/update payload for a custom tool."""

    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="", max_length=1024)
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description="JSON-schema of the tool arguments (OpenAI function format).",
    )
    endpoint: CustomToolEndpoint
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        v = v.strip()
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("name may only contain letters, digits, '-' and '_'")
        return v

    @field_validator("parameters")
    @classmethod
    def _valid_parameters(cls, v: dict) -> dict:
        if not isinstance(v, dict) or v.get("type", "object") != "object":
            raise ValueError("parameters must be a JSON-schema object (type: 'object')")
        return v


class CustomToolOut(BaseModel):
    """A stored custom tool."""

    id: str
    profile_id: str
    name: str
    description: str = ""
    parameters: dict = Field(default_factory=dict)
    endpoint: CustomToolEndpoint
    enabled: bool
    created_at: int
    updated_at: int


class CustomToolTestRequest(BaseModel):
    """Payload for POST /tools/custom/{id}/test — sample arguments to invoke with."""

    arguments: dict = Field(default_factory=dict)


class CustomToolTestResult(BaseModel):
    ok: bool
    result: str
