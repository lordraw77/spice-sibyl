"""
LLM nodes: llm.completion, llm.agent, llm.classify, llm.extract, llm.judge.

All share the model picker (``_candidate_models`` — failover chains), the
response-cached completion (``_cached_complete``) and the strict-JSON helper
(``_llm_json_call``). Depends on ``settings`` and app services (cache, provider
factory, chat) loaded lazily — never the engine. The engine re-exports these
names (for the copilot ``generate/explain/repair`` code and existing tests).
"""

from __future__ import annotations

import json
import logging
import math
import re

import aiosqlite

from app.core.config import settings
from app.workflow.registry import DispatchCtx, node

logger = logging.getLogger(__name__)


# ── shared completion machinery ──────────────────────────────────────────────

async def _cached_complete(request) -> tuple[dict, str]:
    """Complete a chat request through the Phase 19/26 response cache (same dance as
    ChatService.complete — see chat_service.py:275-307), so identical workflow LLM node
    runs skip the provider like chat does. Returns (response_dict, "hit"|"semantic"|"miss").
    cache_service.cache_key() already returns None for tool-bearing/multimodal requests,
    so tool-using llm.agent steps are naturally excluded from caching."""
    from app.services import cache_service
    from app.services.chat_service import ChatService
    from app.services.provider_factory import ProviderFactory

    cache_key = cache_service.cache_key(request)
    cached = cache_service.get(cache_key)
    if cached is not None:
        return ChatService._cached_completion(request, cached, semantic=False), "hit"

    query_embedding: list[float] | None = None
    embed_model: str | None = None
    bucket: str | None = None
    if cache_key is not None and settings.semantic_cache_enabled:
        sem, query_embedding, embed_model, bucket = await cache_service.semantic_get(request)
        if sem is not None:
            return ChatService._cached_completion(request, sem, semantic=True), "semantic"

    provider = ProviderFactory.get_provider(request.model)
    response = await provider.complete(request)
    if hasattr(response, "model_dump"):
        response = response.model_dump()
    try:
        choices = response.get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        cache_service.put(
            cache_key, content, {"usage": response.get("usage") or {}},
            embedding=query_embedding, embed_model=embed_model, bucket=bucket,
        )
    except (AttributeError, TypeError, KeyError, IndexError):
        pass  # non-dict/odd provider response — skip caching
    return response, "miss"


async def _candidate_models(db: aiosqlite.Connection, model: str, failover_chain: str | None) -> list[str]:
    """[model] plus any further models from a named Settings → Models failover chain
    (Phase 31.c), in order, deduplicated. [model] alone when no chain is configured."""
    candidates = [model]
    chain_name = str(failover_chain or "").strip()
    if chain_name:
        from app.db import settings_repository
        from app.schemas.features import MODEL_FAILOVER_CHAINS_OWNER_KEY, failover_chain_models

        blob = await settings_repository.get(db, MODEL_FAILOVER_CHAINS_OWNER_KEY)
        for candidate in failover_chain_models(blob, chain_name):
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _extract_usage(response: dict) -> dict | None:
    """Token counts from a provider response, when it reported any (Phase 30.d
    observability — no per-model cost table exists in the repo yet, so cost is
    intentionally omitted rather than guessed)."""
    usage = response.get("usage") or {}
    if not usage:
        return None
    return {
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
        "tokens_total": usage.get("total_tokens"),
    }


# ── llm.completion ───────────────────────────────────────────────────────────

async def _exec_llm_completion(db: aiosqlite.Connection, profile_id: str, params: dict) -> dict:
    from app.schemas.chat import ChatCompletionRequest, ChatMessage

    model = params.get("model") or settings.default_model
    prompt = params.get("prompt") or params.get("input") or ""
    system = params.get("system")
    messages = []
    if system:
        messages.append(ChatMessage(role="system", content=str(system)))
    messages.append(ChatMessage(role="user", content=str(prompt)))

    candidates = await _candidate_models(db, model, params.get("failover_chain"))
    tried: list[str] = []
    last_exc: Exception | None = None
    for candidate in candidates:
        tried.append(candidate)
        request = ChatCompletionRequest(
            model=candidate, messages=messages, stream=False, profile_id=profile_id
        )
        try:
            response, cache_status = await _cached_complete(request)
        except Exception as exc:  # noqa: BLE001 — fall through to the next chain candidate
            last_exc = exc
            continue
        choices = response.get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
        out = {"content": content, "model": candidate, "_usage": _extract_usage(response), "_cache": cache_status}
        if len(candidates) > 1:
            out["_failover"] = {"tried": tried, "used": candidate}
        return out
    raise last_exc


# ── llm.agent ────────────────────────────────────────────────────────────────

async def _full_tool_definitions(db: aiosqlite.Connection, profile_id: str) -> list[dict]:
    """Built-ins + discovered MCP tools + the profile's custom tools — the same
    set the Phase 18 agent loop uses, so ``llm.agent`` nodes can call them."""
    from app.services import custom_tool_service, mcp_service
    from app.tools.registry import TOOL_DEFINITIONS

    tools = list(TOOL_DEFINITIONS)
    try:
        await mcp_service.refresh(db)
        tools.extend(mcp_service.get_tool_definitions())
    except Exception:  # noqa: BLE001 — a broken MCP server must not block the run
        logger.exception("graph llm.agent: MCP discovery failed; continuing without MCP tools")
    try:
        tools.extend(await custom_tool_service.get_tool_definitions(db, profile_id))
    except Exception:  # noqa: BLE001
        logger.exception("graph llm.agent: custom tool listing failed; continuing without them")
    # Fase 9.1 — the profile's workflows published as tools (active + input
    # contract + expose_as_tool). Namespaced ``workflow__<id>`` so execute_tool
    # routes them to a nested workflow run.
    try:
        from app.services import workflow_tool_service

        tools.extend(await workflow_tool_service.get_tool_definitions(db, profile_id))
    except Exception:  # noqa: BLE001
        logger.exception("graph llm.agent: workflow tool listing failed; continuing without them")
    return tools


async def _exec_llm_agent(db: aiosqlite.Connection, profile_id: str, params: dict) -> dict:
    """Bridge node: run the Phase 18 durable agent loop to completion inline, over
    the full tool set (built-in + MCP + custom)."""
    from app.schemas.chat import ChatCompletionRequest, ChatMessage, ToolCall, ToolCallFunction
    from app.tools.registry import execute_tool

    model = params.get("model") or settings.default_model
    goal = str(params.get("goal") or params.get("prompt") or "")
    max_steps = int(params.get("max_steps") or 8)
    system = params.get("system_prompt") or (
        "You are an autonomous agent. Work towards the goal using the available "
        "tools; when done, reply with the final answer and no further tool calls."
    )
    tools = await _full_tool_definitions(db, profile_id)
    messages = [
        ChatMessage(role="system", content=str(system)),
        ChatMessage(role="user", content=goal),
    ]
    usage_total = {"tokens_in": 0, "tokens_out": 0, "tokens_total": 0}

    def _accumulate(response: dict) -> None:
        step_usage = _extract_usage(response)
        if not step_usage:
            return
        for k in usage_total:
            usage_total[k] += step_usage.get(k) or 0

    candidates = await _candidate_models(db, model, params.get("failover_chain"))
    model_idx = 0  # sticky: once a candidate succeeds, later steps start from it
    tried: list[str] = []

    def _failover_meta(used: str) -> dict | None:
        return {"tried": tried, "used": used} if len(candidates) > 1 else None

    for _ in range(max_steps):
        response = cache_status = last_exc = None
        for idx in range(model_idx, len(candidates)):
            candidate = candidates[idx]
            if candidate not in tried:
                tried.append(candidate)
            request = ChatCompletionRequest(
                model=candidate, messages=messages, tools=tools or None,
                stream=False, profile_id=profile_id,
            )
            try:
                response, cache_status = await _cached_complete(request)
            except Exception as exc:  # noqa: BLE001 — fall through to the next chain candidate
                last_exc = exc
                continue
            model_idx = idx
            model = candidate
            break
        if response is None:
            raise last_exc
        _accumulate(response)
        choices = response.get("choices") or []
        if not choices:
            break
        choice = choices[0]
        msg = choice.get("message") or {}
        tool_calls_raw = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        if choice.get("finish_reason") != "tool_calls" or not tool_calls_raw:
            out = {"content": content, "model": model, "_usage": usage_total, "_cache": cache_status}
            failover = _failover_meta(model)
            if failover:
                out["_failover"] = failover
            return out
        tool_calls = [
            ToolCall(
                id=tc["id"], type=tc.get("type", "function"),
                function=ToolCallFunction(name=tc["function"]["name"], arguments=tc["function"]["arguments"]),
            )
            for tc in tool_calls_raw
        ]
        messages.append(ChatMessage(role="assistant", content=msg.get("content"), tool_calls=tool_calls))
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            try:
                result = await execute_tool(tc.function.name, args, profile_id=profile_id)
            except (RuntimeError, ValueError, OSError) as exc:
                result = f"Error: {exc}"
            messages.append(ChatMessage(role="tool", tool_call_id=tc.id, content=result))

    out = {"content": "Step limit reached without a final answer.", "model": model, "_usage": usage_total}
    failover = _failover_meta(model)
    if failover:
        out["_failover"] = failover
    return out


# ── structured LLM nodes (Phase 35 — roadmap fase 4.1) ─────────────────────

def _parse_llm_json(content: str) -> object:
    """The JSON value inside an LLM reply: tolerates code fences and prose
    around the first JSON object/array. Raises ``ValueError`` when none parses
    (so node retry/onError apply)."""
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError(f"no JSON found in the model reply: {text[:200]!r}")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"model reply is not valid JSON: {exc}") from None
    return value


async def _llm_json_call(
    db: aiosqlite.Connection, profile_id: str, params: dict, system: str, prompt: str
) -> tuple[object, dict]:
    """One completion (with failover chain + response cache, like llm.completion)
    that MUST come back as JSON. Returns (parsed_value, meta)."""
    from app.schemas.chat import ChatCompletionRequest, ChatMessage

    model = params.get("model") or settings.default_model
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=prompt),
    ]
    candidates = await _candidate_models(db, model, params.get("failover_chain"))
    tried: list[str] = []
    last_exc: Exception | None = None
    for candidate in candidates:
        tried.append(candidate)
        request = ChatCompletionRequest(
            model=candidate, messages=messages, stream=False, profile_id=profile_id
        )
        try:
            response, cache_status = await _cached_complete(request)
            choices = response.get("choices") or []
            content = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
            value = _parse_llm_json(content)
        except Exception as exc:  # noqa: BLE001 — a bad reply (call failure or invalid JSON)
            # falls through to the next chain candidate, same as a provider failure
            last_exc = exc
            continue
        meta = {"model": candidate, "_usage": _extract_usage(response), "_cache": cache_status}
        if len(candidates) > 1:
            meta["_failover"] = {"tried": tried, "used": candidate}
        return value, meta
    raise last_exc


def _classify_categories(params: dict) -> list[str]:
    raw = params.get("categories")
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = None
        else:
            raw = [c.strip() for c in text.split(",") if c.strip()]
    if not isinstance(raw, list) or not raw:
        raise ValueError("llm.classify: 'categories' must be a non-empty array (or comma-separated list)")
    return [str(c) for c in raw]


async def _exec_llm_classify(
    db: aiosqlite.Connection, profile_id: str, params: dict, node_input
) -> dict:
    """Guaranteed-structured classification: the model must answer with a JSON
    object whose ``category`` is one of the allowed values — anything else
    raises, so retry/onError apply instead of garbage flowing downstream."""
    categories = _classify_categories(params)
    text = params.get("input") or params.get("text")
    if text is None or str(text) == "":
        text = node_input
    if not isinstance(text, str):
        text = json.dumps(text, default=str, ensure_ascii=False)
    instructions = str(params.get("instructions") or "").strip()
    system = (
        "You are a strict classifier. Reply with ONLY a JSON object — no prose, no code fences — "
        'shaped exactly like {"category": "<one allowed category>", "confidence": <number 0..1>}. '
        f"Allowed categories: {json.dumps(categories, ensure_ascii=False)}."
        + (f" Additional instructions: {instructions}" if instructions else "")
    )
    data, meta = await _llm_json_call(db, profile_id, params, system, text)
    if not isinstance(data, dict):
        raise ValueError("llm.classify: model did not return a JSON object")
    category = str(data.get("category") or "")
    if category not in categories:
        # Tolerate case slips before failing — determinism beats strictness here.
        by_lower = {c.lower(): c for c in categories}
        if category.lower() in by_lower:
            category = by_lower[category.lower()]
        else:
            raise ValueError(f"llm.classify: model returned {category!r}, not one of {categories}")
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = None
    return {"category": category, "confidence": confidence, **meta}


async def _exec_llm_extract(
    db: aiosqlite.Connection, profile_id: str, params: dict, node_input
) -> dict:
    """Guaranteed-structured extraction against a JSON Schema declared in the
    inspector. Top-level ``required`` properties are enforced; a non-conforming
    reply raises, so retry/onError apply."""
    schema = params.get("schema")
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except json.JSONDecodeError as exc:
            raise ValueError(f"llm.extract: 'schema' is not valid JSON: {exc}") from None
    if not isinstance(schema, dict) or not schema:
        raise ValueError("llm.extract: 'schema' must be a JSON Schema object")
    text = params.get("input") or params.get("text")
    if text is None or str(text) == "":
        text = node_input
    if not isinstance(text, str):
        text = json.dumps(text, default=str, ensure_ascii=False)
    instructions = str(params.get("instructions") or "").strip()
    system = (
        "You extract structured data. Reply with ONLY a JSON value matching this JSON Schema "
        "— no prose, no code fences, no extra keys: "
        f"{json.dumps(schema, ensure_ascii=False)}."
        + (f" Additional instructions: {instructions}" if instructions else "")
    )
    data, meta = await _llm_json_call(db, profile_id, params, system, text)
    required = schema.get("required")
    if isinstance(required, list) and isinstance(data, dict):
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(f"llm.extract: model reply is missing required properties: {missing}")
    elif isinstance(required, list) and not isinstance(data, dict):
        raise ValueError("llm.extract: model did not return a JSON object")
    return {"data": data, **meta}


# ── Phase 50 (roadmap fase 18 — LLM quality) ────────────────────────────────

def _judge_scale_max(params: dict) -> int:
    raw = params.get("scaleMax")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = settings.graph_workflow_judge_default_scale_max
    return value if value >= 2 else 2


async def _exec_llm_judge(
    db: aiosqlite.Connection, profile_id: str, params: dict, node_input
) -> tuple[dict, list[str]]:
    """Fase 18.1 — evaluate content against a rubric on a 1..scaleMax scale and
    route to the ``pass``/``fail`` handle by a threshold. The score/threshold
    decides ``passed`` (authoritative), so a generate → judge → regenerate loop
    (`while`) or a quality gate before publishing keeps a deterministic gate even
    when the model's own ``verdict`` disagrees. Shares the model picker, failover
    chain and response cache with the other ``llm.*`` nodes; the judge model can
    differ from the generator's."""
    scale_max = _judge_scale_max(params)
    threshold = params.get("threshold")
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        # Default gate: at least 60% of the scale (rounded up), so a 1..5 scale
        # passes from 3 and a 1..10 scale from 6 without extra configuration.
        threshold = float(math.ceil(scale_max * 0.6))

    criteria = str(params.get("criteria") or "").strip()
    if not criteria:
        raise ValueError("llm.judge: 'criteria' (the rubric to score against) is required")

    content = params.get("input") or params.get("text")
    if content is None or str(content) == "":
        content = node_input
    if not isinstance(content, str):
        content = json.dumps(content, default=str, ensure_ascii=False)

    reference = params.get("reference")
    if reference is not None and not isinstance(reference, str):
        reference = json.dumps(reference, default=str, ensure_ascii=False)
    instructions = str(params.get("instructions") or "").strip()

    system = (
        "You are a strict, impartial evaluator. Score the CONTENT against the CRITERIA on an "
        f"integer scale from 1 to {scale_max} (higher is better). Reply with ONLY a JSON object — "
        'no prose, no code fences — shaped exactly like '
        '{"score": <integer>, "verdict": "pass"|"fail", "rationale": "<one short sentence>"}.'
        + (f" Additional instructions: {instructions}" if instructions else "")
    )
    prompt = f"CRITERIA:\n{criteria}\n\nCONTENT:\n{content}"
    if reference:
        prompt += f"\n\nREFERENCE (the ideal answer to compare against):\n{reference}"

    data, meta = await _llm_json_call(db, profile_id, params, system, prompt)
    if not isinstance(data, dict):
        raise ValueError("llm.judge: model did not return a JSON object")
    raw_score = data.get("score")
    if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
        raise ValueError("llm.judge: model reply is missing a numeric 'score'")
    score = max(1, min(scale_max, int(round(raw_score))))
    passed = score >= threshold
    rationale = data.get("rationale")
    if not isinstance(rationale, str):
        rationale = None
    result = {
        "score": score,
        "scaleMax": scale_max,
        "threshold": threshold,
        "passed": passed,
        "verdict": "pass" if passed else "fail",
        "rationale": rationale,
        **meta,
    }
    return result, ["pass" if passed else "fail"]


# ── handlers ─────────────────────────────────────────────────────────────────

@node("llm.completion")
async def _h_llm_completion(c: DispatchCtx):
    return await _exec_llm_completion(c.db, c.profile_id, c.params), ["main"]


@node("llm.agent")
async def _h_llm_agent(c: DispatchCtx):
    return await _exec_llm_agent(c.db, c.profile_id, c.params), ["main"]


@node("llm.classify")
async def _h_llm_classify(c: DispatchCtx):
    return await _exec_llm_classify(c.db, c.profile_id, c.params, c.node_input), ["main"]


@node("llm.extract")
async def _h_llm_extract(c: DispatchCtx):
    return await _exec_llm_extract(c.db, c.profile_id, c.params, c.node_input), ["main"]


@node("llm.judge")
async def _h_llm_judge(c: DispatchCtx):
    return await _exec_llm_judge(c.db, c.profile_id, c.params, c.node_input)
