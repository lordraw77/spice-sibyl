"""
Provider adapter that proxies to the Multi-MCP orchestrator sidecar.

The sidecar (``agent_server.py`` in the multi-mcp project) exposes an
OpenAI-compatible ``/v1/chat/completions`` endpoint backed by ``main_agent``'s
own provider rotation pool and Docker MCP sub-agents. This adapter simply
forwards requests to it, so the orchestrator appears as the ``agent/*`` model
family in the gateway and is usable from both the web console and Telegram with
no channel-specific code.

Configuration (see app.core.config.Settings):
  ORCHESTRATOR_BASE_URL  — sidecar base, e.g. http://host.docker.internal:8910/v1
  ORCHESTRATOR_TIMEOUT   — read timeout in seconds (orchestrator turns are slow)
"""

import json

import httpx

from app.core.config import settings
from app.core.logging_context import get_request_id
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest


class OrchestratorProvider(BaseProvider):
    MODEL_ID = "agent/multi-mcp"

    def _base_url(self) -> str:
        base = settings.orchestrator_base_url
        if not base:
            raise RuntimeError(
                "ORCHESTRATOR_BASE_URL is not configured — the Multi-MCP "
                "orchestrator sidecar is unavailable."
            )
        return base.rstrip("/")

    def _timeout(self) -> httpx.Timeout:
        # The orchestrator spawns Docker containers and may chain sub-agent
        # calls, so the read timeout must cover the whole turn.
        return httpx.Timeout(settings.orchestrator_timeout, connect=10.0)

    def _payload(self, request: ChatCompletionRequest, stream: bool) -> dict:
        return {
            "model": request.model,
            "messages": [
                {"role": m.role, "content": m.content or ""}
                for m in request.messages
                if m.role in ("system", "user", "assistant")
            ],
            "stream": stream,
        }

    @staticmethod
    def _headers() -> dict[str, str]:
        """Propagate the request id so the sidecar can correlate its own logs."""
        rid = get_request_id()
        return {"X-Request-ID": rid} if rid else {}

    async def complete(self, request: ChatCompletionRequest):
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            resp = await client.post(
                f"{self._base_url()}/chat/completions",
                json=self._payload(request, stream=False),
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def stream(self, request: ChatCompletionRequest):
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            async with client.stream(
                "POST",
                f"{self._base_url()}/chat/completions",
                json=self._payload(request, stream=True),
                headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        continue

    async def list_models(self):
        return [{"id": self.MODEL_ID, "object": "model", "owned_by": "multi-mcp"}]
