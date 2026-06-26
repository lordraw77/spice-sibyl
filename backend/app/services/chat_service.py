"""
ChatService — orchestration layer between endpoints and providers.

When tools are present in the request, the service runs a server-side
tool-execution loop using non-streaming calls, emitting SSE events
for each tool invocation and result before streaming the final text reply.
"""

import json
import logging
import time

from sse_starlette.sse import EventSourceResponse

from app.data.model_catalog import get_model_metadata
from app.schemas.chat import ChatCompletionRequest, ChatMessage, ToolCall, ToolCallFunction
from app.services.provider_factory import ProviderFactory
from app.tools.registry import execute_tool

logger = logging.getLogger(__name__)

_MAX_TOOL_ITERATIONS = 5


def _message_text(content) -> str:
    """Extract plain text from a message content (string or multimodal parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


class ChatService:
    """Orchestrates chat completions, streaming, and server-side tool execution."""

    async def _apply_rag(self, request: ChatCompletionRequest):
        """If RAG is enabled, retrieve context and return (request, sources).

        The retrieved context is folded into the *last user message* rather than a
        separate system message: many chat templates (e.g. Nemotron) only honor a
        single leading system message and silently drop ones inserted mid-thread,
        so augmenting the user turn is the reliable cross-provider grounding point.
        Returns the original request and an empty list when RAG is disabled or
        nothing relevant is found.  Retrieval failures are logged and swallowed.
        """
        if not request.rag:
            return request, []

        # Lazy imports keep RAG optional and avoid a hard numpy dependency on the
        # hot path when the feature is unused.
        import aiosqlite
        from app.core.config import settings
        from app.services import rag_service

        # Find the last user message and a plain-text query for retrieval.
        last_idx = next(
            (i for i in range(len(request.messages) - 1, -1, -1)
             if request.messages[i].role == "user"),
            None,
        )
        if last_idx is None:
            return request, []
        last_user = request.messages[last_idx]
        query = _message_text(last_user.content)
        if not query.strip():
            return request, []

        profile_id = request.profile_id or "default"
        top_k = request.rag_top_k or 4
        try:
            db = await aiosqlite.connect(settings.db_path)
            db.row_factory = aiosqlite.Row
            try:
                await db.execute("PRAGMA foreign_keys=ON")
                sources = await rag_service.retrieve(
                    db, profile_id, query, top_k=top_k
                )
            finally:
                await db.close()
        except Exception:
            logger.exception("RAG retrieval failed; continuing without context")
            return request, []

        if not sources:
            logger.info("RAG: enabled but no relevant context for profile=%s model=%s", profile_id, request.model)
            return request, []

        logger.info(
            "RAG: injecting %d source(s) into model=%s profile=%s — %s",
            len(sources), request.model, profile_id,
            ", ".join(f"{s.filename}#{s.chunk_index}({s.score:.2f})" for s in sources),
        )
        context = rag_service.build_context_block(sources)
        augmented = f"{context}\n\n---\n\nDomanda dell'utente:\n{query}"

        messages = list(request.messages)
        original = messages[last_idx]
        if isinstance(original.content, list):
            # Multimodal: replace the leading text part (or prepend one) with the
            # augmented text, preserving image parts.
            new_parts: list = []
            text_done = False
            for part in original.content:
                if not text_done and isinstance(part, dict) and part.get("type") == "text":
                    new_parts.append({"type": "text", "text": augmented})
                    text_done = True
                else:
                    new_parts.append(part)
            if not text_done:
                new_parts.insert(0, {"type": "text", "text": augmented})
            new_content: object = new_parts
        else:
            new_content = augmented
        messages[last_idx] = original.model_copy(update={"content": new_content})
        new_request = request.model_copy(update={"messages": messages})
        return new_request, sources

    @staticmethod
    def _rag_frame(sources) -> dict:
        """Build an SSE control frame carrying the RAG sources for the UI."""
        return {
            "event": "rag_context",
            "data": json.dumps({"sources": [s.model_dump() for s in sources]}),
        }

    async def complete(self, request: ChatCompletionRequest):
        """Return a single non-streaming completion."""
        request, _ = await self._apply_rag(request)
        provider = ProviderFactory.get_provider(request.model)
        return await provider.complete(request)

    async def stream(self, request: ChatCompletionRequest):
        """Return an SSE EventSourceResponse for a streaming completion."""
        request, rag_sources = await self._apply_rag(request)
        provider = ProviderFactory.get_provider(request.model)

        # agent/* models orchestrate their own sub-agents; never wrap them in the
        # server-side tool loop — they stream their own tool_call/tool_result frames.
        is_agent = bool(request.model and request.model.startswith("agent/"))
        if request.tools and not is_agent:
            return EventSourceResponse(self._stream_with_tools(provider, request, rag_sources))

        async def _plain():
            try:
                if rag_sources:
                    yield self._rag_frame(rag_sources)
                async for chunk in provider.stream(request):
                    # A provider may emit control frames carrying a named SSE event
                    # (e.g. the orchestrator's tool_call / tool_result progress).
                    event = "message"
                    if isinstance(chunk, dict) and "_sse_event" in chunk:
                        chunk = dict(chunk)
                        event = chunk.pop("_sse_event")
                    yield {"event": event, "data": json.dumps(chunk)}
                yield {"event": "done", "data": "[DONE]"}
            except (RuntimeError, OSError, ValueError) as exc:
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}

        return EventSourceResponse(_plain())

    async def _stream_with_tools(self, provider, request: ChatCompletionRequest, rag_sources=None):
        """Run the server-side tool-execution loop, yielding SSE-compatible dicts."""
        messages: list[ChatMessage] = list(request.messages)

        try:
            if rag_sources:
                yield self._rag_frame(rag_sources)
            for _ in range(_MAX_TOOL_ITERATIONS):
                call_req = request.model_copy(
                    update={"messages": messages, "stream": False}
                )
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
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": content},
                                    "finish_reason": None,
                                }
                            ],
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
                        "data": json.dumps(
                            {"id": tc.id, "name": func_name, "arguments": func_args}
                        ),
                    }

                    try:
                        result = await execute_tool(func_name, func_args)
                    except (RuntimeError, ValueError, OSError) as exc:
                        result = f"Error: {exc}"

                    yield {
                        "event": "tool_result",
                        "data": json.dumps(
                            {"id": tc.id, "name": func_name, "result": result}
                        ),
                    }

                    messages.append(ChatMessage(
                        role="tool",
                        tool_call_id=tc.id,
                        content=result,
                    ))
            else:
                # for-loop exhausted without a break — max iterations reached
                logger.warning(
                    "Tool loop hit max iterations (%d) for model=%s",
                    _MAX_TOOL_ITERATIONS,
                    request.model,
                )
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "message": (
                            f"Tool call limit reached ({_MAX_TOOL_ITERATIONS} iterations). "
                            "The model kept requesting tools without producing a final answer."
                        )
                    }),
                }

            yield {"event": "done", "data": "[DONE]"}

        except (RuntimeError, OSError, ValueError) as exc:
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    async def list_models(self, model: str):
        """Return the model list from the provider that handles the given model id."""
        provider = ProviderFactory.get_provider(model)
        return await provider.list_models()
