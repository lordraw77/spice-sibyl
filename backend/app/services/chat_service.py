"""
ChatService — orchestration layer between endpoints and providers.

When tools are present in the request, the service runs a server-side
tool-execution loop using non-streaming calls, emitting SSE events
for each tool invocation and result before streaming the final text reply.
"""

import json
import time

from sse_starlette.sse import EventSourceResponse

from app.data.model_catalog import get_model_metadata
from app.schemas.chat import ChatCompletionRequest, ChatMessage, ToolCall, ToolCallFunction
from app.services.provider_factory import ProviderFactory

_MAX_TOOL_ITERATIONS = 5


class ChatService:
    async def complete(self, request: ChatCompletionRequest):
        provider = ProviderFactory.get_provider(request.model)
        return await provider.complete(request)

    async def stream(self, request: ChatCompletionRequest):
        provider = ProviderFactory.get_provider(request.model)

        # agent/* models orchestrate their own sub-agents; never wrap them in the
        # server-side tool loop — they stream their own tool_call/tool_result frames.
        is_agent = bool(request.model and request.model.startswith("agent/"))
        if request.tools and not is_agent:
            return EventSourceResponse(self._stream_with_tools(provider, request))

        async def _plain():
            try:
                async for chunk in provider.stream(request):
                    # A provider may emit control frames carrying a named SSE event
                    # (e.g. the orchestrator's tool_call / tool_result progress).
                    event = "message"
                    if isinstance(chunk, dict) and "_sse_event" in chunk:
                        chunk = dict(chunk)
                        event = chunk.pop("_sse_event")
                    yield {"event": event, "data": json.dumps(chunk)}
                yield {"event": "done", "data": "[DONE]"}
            except Exception as exc:
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}

        return EventSourceResponse(_plain())

    async def _stream_with_tools(self, provider, request: ChatCompletionRequest):
        from app.tools.registry import execute_tool

        messages: list[ChatMessage] = list(request.messages)

        try:
            for _ in range(_MAX_TOOL_ITERATIONS):
                call_req = request.model_copy(update={"messages": messages, "stream": False})
                response = await provider.complete(call_req)

                choices = response.get("choices") or []
                if not choices:
                    break

                choice = choices[0]
                finish_reason = choice.get("finish_reason")
                msg = choice.get("message") or {}
                tool_calls_raw = msg.get("tool_calls") or []

                if finish_reason != "tool_calls" or not tool_calls_raw:
                    # No more tool calls — emit final content + meta
                    content = msg.get("content") or ""
                    yield {
                        "event": "message",
                        "data": json.dumps({
                            "object": "chat.completion.chunk",
                            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                        }),
                    }
                    metrics = response.get("metrics") or {}
                    usage = response.get("usage") or {}
                    model_meta = get_model_metadata(request.model)
                    yield {
                        "event": "message",
                        "data": json.dumps({
                            "id": f"meta-{int(time.time())}",
                            "object": "chat.completion.meta",
                            "created": int(time.time()),
                            "model": request.model,
                            "choices": [{
                                "index": 0,
                                "finish_reason": finish_reason,
                                "message": {
                                    "role": "assistant",
                                    "content": content,
                                    "model": request.model,
                                    "provider": model_meta.get("provider"),
                                    "latency_ms": metrics.get("latency_ms"),
                                    "first_token_ms": metrics.get("first_token_ms"),
                                    "prompt_tokens": usage.get("prompt_tokens"),
                                    "completion_tokens": usage.get("completion_tokens"),
                                    "total_tokens": usage.get("total_tokens"),
                                    "tokens_per_second": metrics.get("tokens_per_second"),
                                    "finish_reason": finish_reason,
                                    "created_at": int(time.time()),
                                    "capabilities": model_meta.get("capabilities", []),
                                    "free": model_meta.get("free", False),
                                },
                            }],
                            "usage": usage,
                            "metrics": metrics,
                        }),
                    }
                    break

                # Build typed ToolCall list
                tool_calls = [
                    ToolCall(
                        id=tc["id"],
                        type=tc.get("type", "function"),
                        function=ToolCallFunction(
                            name=tc["function"]["name"],
                            arguments=tc["function"]["arguments"],
                        ),
                    )
                    for tc in tool_calls_raw
                ]

                # Append assistant message with tool_calls to history
                messages.append(ChatMessage(
                    role="assistant",
                    content=msg.get("content"),
                    tool_calls=tool_calls,
                ))

                # Execute each tool and stream events
                for tc in tool_calls:
                    func_name = tc.function.name
                    try:
                        func_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        func_args = {}

                    yield {
                        "event": "tool_call",
                        "data": json.dumps({"id": tc.id, "name": func_name, "arguments": func_args}),
                    }

                    try:
                        result = await execute_tool(func_name, func_args)
                    except Exception as exc:
                        result = f"Error: {exc}"

                    yield {
                        "event": "tool_result",
                        "data": json.dumps({"id": tc.id, "name": func_name, "result": result}),
                    }

                    messages.append(ChatMessage(
                        role="tool",
                        tool_call_id=tc.id,
                        content=result,
                    ))

            yield {"event": "done", "data": "[DONE]"}

        except Exception as exc:
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    async def list_models(self, model: str):
        provider = ProviderFactory.get_provider(model)
        return await provider.list_models()
