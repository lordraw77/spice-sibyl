import asyncio
import time
import uuid

from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionChoice, ChatCompletionRequest, ChatCompletionResponse, ChatMessage


class MockProvider(BaseProvider):
    async def complete(self, request: ChatCompletionRequest):
        prompt = request.messages[-1].content if request.messages else ""
        content = f"SpiceSibyl mock response: ricevuto '{prompt}'. Integra qui LiteLLM/provider reale."
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
        prompt = request.messages[-1].content if request.messages else ""
        tokens = [
            "SpiceSibyl", " ", "stream", " ", "mock", ": ",
            f"ricevuto '{prompt}'"
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
        yield {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }

    async def list_models(self):
        return [{"id": "mock/spice-sibyl-1", "object": "model", "owned_by": "spice-sibyl"}]
