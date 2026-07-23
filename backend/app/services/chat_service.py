"""
ChatService — orchestration layer between endpoints and providers.

When tools are present in the request, the service runs a server-side
tool-execution loop using non-streaming calls, emitting SSE events
for each tool invocation and result before streaming the final text reply.
"""

import json
import logging
import time

import httpx
from sse_starlette.sse import EventSourceResponse

from app.core import metrics
from app.core.config import settings
from app.data.model_catalog import get_model_metadata
from app.db import pool
from app.schemas.chat import ChatCompletionRequest, ChatMessage, ToolCall, ToolCallFunction
from app.services import cache_service, key_resolver
from app.services.provider_factory import ProviderFactory
from app.tools.registry import execute_tool

logger = logging.getLogger(__name__)


def _parse_fallback_chain() -> list[tuple[str, str]]:
    """Parse CHAT_FALLBACK_CHAIN into (provider, model) pairs."""
    raw = settings.chat_fallback_chain or ""
    out: list[tuple[str, str]] = []
    for token in raw.split(","):
        token = token.strip()
        if ":" not in token:
            continue
        provider, model = token.split(":", 1)
        out.append((provider.strip(), model.strip()))
    return out


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
            db = await pool.checkout()
            try:
                await db.execute("PRAGMA foreign_keys=ON")
                sources = await rag_service.retrieve(
                    db, profile_id, query, top_k=top_k,
                    document_ids=request.rag_document_ids or None,
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

    async def _apply_memory(self, request: ChatCompletionRequest):
        """Phase 19: inject the profile's <user_memory> block into the system prompt.

        Returns (request, injected: bool). Skipped when the request carries
        memory:false (incognito), the profile switch is off, or there are no
        enabled memories. Failures are logged and swallowed.
        """
        if not request.memory or not settings.memory_enabled:
            return request, False

        from app.services import memory_service

        profile_id = request.profile_id or "default"
        try:
            db = await pool.checkout()
            try:
                block = await memory_service.load_memory_block(db, profile_id)
            finally:
                await db.close()
        except Exception:
            logger.exception("Memory injection failed; continuing without it")
            return request, False

        if not block:
            return request, False
        logger.info("Memory: injecting %d chars for profile=%s model=%s",
                    len(block), profile_id, request.model)
        return memory_service.apply_memory_block(request, block), True

    @staticmethod
    def _memory_frame() -> dict:
        """SSE control frame telling the UI this reply is memory-grounded (🧠 chip)."""
        return {"event": "memory_context", "data": json.dumps({"used": True})}

    @staticmethod
    def _cached_completion(request: ChatCompletionRequest, cached: dict, semantic: bool = False) -> dict:
        """Build a non-streaming completion response replayed from cache.

        `semantic` flags a Phase 26 fuzzy hit (⚡~ chip) vs an exact 19.c hit.
        """
        now = int(time.time())
        message = {"role": "assistant", "content": cached["content"], "cached": True}
        if semantic:
            message["cached_semantic"] = True
        return {
            "id": f"cached-{now}",
            "object": "chat.completion",
            "created": now,
            "model": request.model,
            "cached": True,
            "cached_semantic": semantic,
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            "usage": (cached.get("meta") or {}).get("usage") or {},
            "metrics": {"latency_ms": 0, "cached": True, "cached_semantic": semantic},
        }

    @staticmethod
    def _cached_reply_frames(
        request: ChatCompletionRequest, cached: dict, semantic: bool = False
    ) -> list[dict]:
        """Replay a cached completion as a single chunk + meta + done.

        `semantic` flags a Phase 26 fuzzy hit (⚡~ chip) vs an exact 19.c hit.
        """
        meta = dict(cached.get("meta") or {})
        meta_msg = dict((meta.get("choices") or [{}])[0].get("message") or {}) if meta else {}
        meta_msg.update({"content": cached["content"], "cached": True})
        if semantic:
            meta_msg["cached_semantic"] = True
        now = int(time.time())
        frames = [
            {
                "event": "message",
                "data": json.dumps({
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": cached["content"]},
                                 "finish_reason": None}],
                }),
            },
            {
                "event": "message",
                "data": json.dumps({
                    "id": f"meta-cached-{now}",
                    "object": "chat.completion.meta",
                    "created": now,
                    "model": request.model,
                    "cached": True,
                    "cached_semantic": semantic,
                    "choices": [{"index": 0, "finish_reason": "stop", "message": meta_msg}],
                    "usage": meta.get("usage") or {},
                    "metrics": {"latency_ms": 0, "cached": True, "cached_semantic": semantic},
                }),
            },
            {"event": "done", "data": "[DONE]"},
        ]
        return frames

    @staticmethod
    def _rag_frame(sources) -> dict:
        """Build an SSE control frame carrying the RAG sources for the UI."""
        return {
            "event": "rag_context",
            "data": json.dumps({"sources": [s.model_dump() for s in sources]}),
        }

    @staticmethod
    def _fallback_candidates(model: str) -> list[tuple[str | None, str]]:
        """Build the ordered (provider, model) list: requested model first, then
        each configured CHAT_FALLBACK_CHAIN entry (skipping the requested model and
        unconfigured providers)."""
        candidates: list[tuple[str | None, str]] = [(None, model)]
        for provider, fb_model in _parse_fallback_chain():
            if fb_model == model:
                continue
            if not key_resolver.is_configured(provider):
                continue
            candidates.append((provider, fb_model))
        return candidates

    @staticmethod
    def _record_chat_metrics(provider: str, model: str, meta_chunk: dict, start: float) -> None:
        """Record per-provider token + latency series from a meta frame."""
        usage = meta_chunk.get("usage") or {}
        metrics_data = meta_chunk.get("metrics") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if prompt_tokens:
            metrics.provider_tokens_total.labels(provider, model, "prompt").inc(prompt_tokens)
        if completion_tokens:
            metrics.provider_tokens_total.labels(provider, model, "completion").inc(completion_tokens)
        latency_ms = metrics_data.get("latency_ms")
        seconds = (latency_ms / 1000.0) if latency_ms else (time.perf_counter() - start)
        metrics.provider_latency_seconds.labels(provider, model).observe(seconds)

    async def complete(self, request: ChatCompletionRequest):
        """Return a single non-streaming completion."""
        request, _ = await self._apply_memory(request)
        request, _ = await self._apply_rag(request)

        cache_key = cache_service.cache_key(request)
        cached = cache_service.get(cache_key)
        if cached is not None:
            logger.info("Response cache hit (complete) for model=%s", request.model)
            return self._cached_completion(request, cached, semantic=False)

        # Exact-match miss: try the semantic layer, reusing its query embedding on
        # the ensuing put() so a store never costs a second embedding call.
        query_embedding: list[float] | None = None
        embed_model: str | None = None
        bucket: str | None = None
        if cache_key is not None and settings.semantic_cache_enabled:
            sem, query_embedding, embed_model, bucket = await cache_service.semantic_get(request)
            if sem is not None:
                return self._cached_completion(request, sem, semantic=True)

        provider = ProviderFactory.get_provider(request.model)
        response = await provider.complete(request)
        try:
            choices = response.get("choices") or []
            content = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
            cache_service.put(
                cache_key, content, {"usage": response.get("usage") or {}},
                embedding=query_embedding, embed_model=embed_model, bucket=bucket,
            )
        except (AttributeError, TypeError, KeyError, IndexError):
            pass  # non-dict/odd provider response — skip caching
        return response

    async def stream(self, request: ChatCompletionRequest):
        """Return an SSE EventSourceResponse for a streaming completion."""
        request, memory_used = await self._apply_memory(request)
        request, rag_sources = await self._apply_rag(request)
        provider = ProviderFactory.get_provider(request.model)

        # agent/* models orchestrate their own sub-agents; never wrap them in the
        # server-side tool loop — they stream their own tool_call/tool_result frames.
        is_agent = bool(request.model and request.model.startswith("agent/"))
        if request.tools and not is_agent:
            return EventSourceResponse(
                self._stream_with_tools(provider, request, rag_sources, memory_used)
            )

        # agent/* models manage their own provider rotation; don't apply our
        # gateway-level fallback chain to them.
        candidates = (
            [(None, request.model)] if is_agent
            else self._fallback_candidates(request.model)
        )

        cache_key = None if is_agent else cache_service.cache_key(request)

        async def _plain():
            metrics.active_sse_streams.inc()
            try:
                if memory_used:
                    yield self._memory_frame()
                if rag_sources:
                    yield self._rag_frame(rag_sources)

                cached = cache_service.get(cache_key)
                if cached is not None:
                    logger.info("Response cache hit (stream) for model=%s", request.model)
                    for frame in self._cached_reply_frames(request, cached):
                        yield frame
                    return

                # Exact-match miss: try the semantic layer, reusing its query
                # embedding on the ensuing put() to avoid a second embedding call.
                query_embedding: list[float] | None = None
                embed_model: str | None = None
                bucket: str | None = None
                if cache_key is not None and settings.semantic_cache_enabled:
                    sem, query_embedding, embed_model, bucket = await cache_service.semantic_get(request)
                    if sem is not None:
                        for frame in self._cached_reply_frames(request, sem, semantic=True):
                            yield frame
                        return

                full_content = ""
                meta_chunk: dict = {}
                produced = False
                for idx, (prov_hint, model) in enumerate(candidates):
                    req = request if model == request.model else request.model_copy(update={"model": model})
                    attempt_provider = ProviderFactory.get_provider(model)
                    meta = get_model_metadata(model)
                    provider_label = meta.get("provider") or prov_hint or "unknown"
                    start = time.perf_counter()
                    try:
                        async for chunk in attempt_provider.stream(req):
                            # A provider may emit control frames carrying a named SSE
                            # event (e.g. the orchestrator's tool_call progress).
                            event = "message"
                            if isinstance(chunk, dict) and "_sse_event" in chunk:
                                chunk = dict(chunk)
                                event = chunk.pop("_sse_event")
                            produced = True
                            if isinstance(chunk, dict) and chunk.get("object") == "chat.completion.meta":
                                meta_chunk = chunk
                                self._record_chat_metrics(provider_label, model, chunk, start)
                            elif isinstance(chunk, dict) and event == "message":
                                delta_choices = chunk.get("choices") or []
                                if delta_choices:
                                    full_content += (delta_choices[0].get("delta") or {}).get("content") or ""
                            yield {"event": event, "data": json.dumps(chunk)}
                        metrics.provider_requests_total.labels(provider_label, model, "success").inc()
                        cache_service.put(
                            cache_key, full_content, {"usage": meta_chunk.get("usage") or {}},
                            embedding=query_embedding, embed_model=embed_model, bucket=bucket,
                        )
                        yield {"event": "done", "data": "[DONE]"}
                        return
                    except Exception as exc:  # noqa: BLE001 — fallback must catch any provider failure
                        metrics.provider_requests_total.labels(provider_label, model, "error").inc()
                        logger.warning("Chat provider %s (%s) failed: %s", provider_label, model, exc)
                        # Once output has reached the client we can't safely switch
                        # providers (would duplicate/garble the reply): surface the error.
                        if produced or idx + 1 >= len(candidates):
                            yield {"event": "error", "data": json.dumps({"message": str(exc)})}
                            return
                        next_model = candidates[idx + 1][1]
                        yield {
                            "event": "provider_switch",
                            "data": json.dumps({"from": model, "to": next_model, "reason": str(exc)}),
                        }
            finally:
                metrics.active_sse_streams.dec()

        return EventSourceResponse(_plain())

    async def _stream_with_tools(
        self, provider, request: ChatCompletionRequest, rag_sources=None, memory_used=False
    ):
        """Run the server-side tool-execution loop, yielding SSE-compatible dicts."""
        messages: list[ChatMessage] = list(request.messages)

        metrics.active_sse_streams.inc()
        try:
            if memory_used:
                yield self._memory_frame()
            if rag_sources:
                yield self._rag_frame(rag_sources)
            max_tool_iterations = (
                request.max_tool_iterations or settings.chat_max_tool_iterations
            )
            for _ in range(max_tool_iterations):
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
                    # NB: don't shadow the module-level `metrics` (used for
                    # active_sse_streams) — that turns it into a function-local
                    # and breaks the .inc()/.dec() calls with UnboundLocalError.
                    resp_metrics = response.get("metrics") or {}
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
                                    "latency_ms": resp_metrics.get("latency_ms"),
                                    "first_token_ms": resp_metrics.get("first_token_ms"),
                                    "prompt_tokens": usage.get("prompt_tokens"),
                                    "completion_tokens": usage.get("completion_tokens"),
                                    "total_tokens": usage.get("total_tokens"),
                                    "tokens_per_second": resp_metrics.get("tokens_per_second"),
                                    "finish_reason": finish_reason,
                                    "created_at": int(time.time()),
                                    "capabilities": model_meta.get("capabilities", []),
                                    "free": model_meta.get("free", False),
                                },
                            }],
                            "usage": usage,
                            "metrics": resp_metrics,
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
                        result = await execute_tool(
                            func_name, func_args, profile_id=request.profile_id or "default"
                        )
                    except (RuntimeError, ValueError, OSError) as exc:
                        result = f"Error: {exc}"

                    yield {
                        "event": "tool_result",
                        "data": json.dumps(
                            {"id": tc.id, "name": func_name, "result": result}
                        ),
                    }

                    # generate_image returns a huge base64 data-URI for the UI;
                    # feed the model a short placeholder instead of blowing the
                    # context window with encoded pixels.
                    model_result = result
                    if func_name == "generate_image" and result.startswith("!["):
                        model_result = "[Image generated successfully and shown to the user.]"

                    messages.append(ChatMessage(
                        role="tool",
                        tool_call_id=tc.id,
                        content=model_result,
                    ))
            else:
                # for-loop exhausted without a break — max iterations reached
                logger.warning(
                    "Tool loop hit max iterations (%d) for model=%s",
                    max_tool_iterations,
                    request.model,
                )
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "message": (
                            f"Tool call limit reached ({max_tool_iterations} iterations). "
                            "The model kept requesting tools without producing a final answer."
                        )
                    }),
                }

            yield {"event": "done", "data": "[DONE]"}

        except httpx.HTTPStatusError as exc:
            # Provider returned an HTTP error (e.g. 429 rate limit): surface a
            # readable message to the UI instead of crashing the SSE stream.
            status = exc.response.status_code
            reason = exc.response.reason_phrase or ""
            detail = ""
            try:
                body = exc.response.json()
                detail = body.get("title") or body.get("detail") or body.get("message") or ""
            except (json.JSONDecodeError, ValueError, AttributeError):
                pass
            message = f"Provider error {status} {reason}".strip()
            if detail and detail.lower() != reason.lower():
                message += f": {detail}"
            logger.warning(
                "Tool loop provider HTTP error %s for model=%s: %s",
                status, request.model, message,
            )
            yield {"event": "error", "data": json.dumps({"message": message, "status": status})}
            yield {"event": "done", "data": "[DONE]"}
        except (RuntimeError, OSError, ValueError, httpx.HTTPError) as exc:
            logger.warning("Tool loop failed for model=%s: %s", request.model, exc)
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}
            yield {"event": "done", "data": "[DONE]"}
        finally:
            metrics.active_sse_streams.dec()

    async def list_models(self, model: str):
        """Return the model list from the provider that handles the given model id."""
        provider = ProviderFactory.get_provider(model)
        return await provider.list_models()
