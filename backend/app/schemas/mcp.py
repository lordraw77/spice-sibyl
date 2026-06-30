"""
Phase 18 — MCP server registry schemas.

A server is stored in the standard ``mcpServers`` config shape, e.g.::

    {
      "mcpServers": {
        "wikillm": {
          "command": "docker",
          "args": ["run", "--rm", "-i", "lordraw/llmwiki:latest", "python", "run_stdio.py"],
          "env": {"FOO": "bar"}
        }
      }
    }

``McpServerConfig`` is the per-server object; ``McpConfigBundle`` is the full
``{"mcpServers": {...}}`` document used for import/export.
"""

from pydantic import BaseModel, Field, field_validator, model_validator

_SSE_TRANSPORTS = ("sse", "http", "streamable-http")


class McpServerConfig(BaseModel):
    """One MCP server, in the standard `mcpServers` shape. Two transports:

    * **stdio** — launched locally via ``command``/``args``/``env``/``cwd``.
    * **sse** — a remote server reached over HTTP+SSE at ``url`` (optional
      ``headers``, e.g. for auth).

    ``type`` is optional and inferred when omitted (``url`` → sse, else stdio).
    Extra keys are tolerated and round-tripped.
    """

    type: str | None = Field(default=None, description="'stdio' | 'sse'. Inferred when omitted.")
    # stdio transport
    command: str | None = Field(default=None, description="Executable to launch (stdio).")
    args: list[str] = Field(default_factory=list, description="Arguments passed to the command.")
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables.")
    cwd: str | None = Field(default=None, description="Working directory for the process.")
    # sse / http transport
    url: str | None = Field(default=None, description="SSE endpoint URL (sse transport).")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP headers (sse transport).")

    # Tolerate (and round-trip) other standard keys without rejecting the config.
    model_config = {"extra": "allow"}

    @property
    def transport(self) -> str:
        """Resolve the effective transport ('stdio' or 'sse')."""
        t = (self.type or "").lower()
        if t in _SSE_TRANSPORTS:
            return "sse"
        if t == "stdio":
            return "stdio"
        return "sse" if self.url else "stdio"

    @field_validator("command")
    @classmethod
    def _command_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("command must not be empty")
        return v

    @model_validator(mode="after")
    def _require_transport_fields(self) -> "McpServerConfig":
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP server requires 'command'")
        if self.transport == "sse" and not self.url:
            raise ValueError("sse MCP server requires 'url'")
        return self


class McpServerIn(BaseModel):
    """Create/update payload: a name plus its standard config + enabled flag."""

    name: str = Field(..., min_length=1, max_length=64)
    config: McpServerConfig
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        v = v.strip()
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("name may only contain letters, digits, '-' and '_'")
        return v


class McpToolInfo(BaseModel):
    """A tool discovered from a connected MCP server."""

    name: str
    description: str = ""
    input_schema: dict = Field(default_factory=dict)


class McpServerOut(BaseModel):
    """A registered MCP server with live health/discovery state."""

    id: str
    name: str
    config: McpServerConfig
    enabled: bool
    created_at: int
    updated_at: int
    # Live state (populated on demand; null when not yet probed)
    status: str = "unknown"          # 'ok' | 'error' | 'disabled' | 'unknown'
    error: str | None = None
    tools: list[McpToolInfo] = Field(default_factory=list)


class McpConfigBundle(BaseModel):
    """The standard ``{"mcpServers": {...}}`` import/export document."""

    mcpServers: dict[str, McpServerConfig] = Field(default_factory=dict)
