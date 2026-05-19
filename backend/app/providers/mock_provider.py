"""
Mock provider — used for local development and testing without real API keys.

Activated when:
  - the model string starts with 'mock/'
  - LITELLM_PROVIDER=mock is set in the environment

The stream method introduces a small delay between tokens (80 ms) to simulate
realistic streaming latency in the frontend.
"""

import asyncio
import time
import uuid

from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionChoice, ChatCompletionRequest, ChatCompletionResponse, ChatMessage


class MockProvider(BaseProvider):
    async def complete(self, request: ChatCompletionRequest):
        """Return a deterministic echo response wrapping the last user message."""
        prompt = request.messages[-1].content if request.messages else ""
        content = f"SpiceSibyl mock response: received '{prompt}'. Replace with LiteLLM / real provider."
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=request.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    finish_reason="stop",
                    message=ChatMessage(role="assistant", content=content),
                )
            ],
        )

    async def stream(self, request: ChatCompletionRequest):
        """Yield token-by-token chunks with a simulated 80 ms inter-token delay."""
        prompt = request.messages[-1].content if request.messages else ""
        tokens = [
            "SpiceSibyl", " ", "stream", " ", "mock", ": ",
            f"received '{prompt}'"
        ]
        for token in tokens:
            yield {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": request.model,
                "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
            }
            await asyncio.sleep(0.08)
        # Terminal chunk signals end-of-stream
        yield {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }

    async def list_models(self):
        """Expose a single mock model entry for the model selector."""
        return [{"id": "mock/spice-sibyl-1", "object": "model", "owned_by": "spice-sibyl"}]
