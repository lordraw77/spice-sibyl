"""
Multimodal nodes: audio.transcribe, image.ocr, image.generate, tts.

Roadmap fase 15.5 — the last deferred item of the workflow roadmap. All four
speak the same dialect as the rest of the file-handling nodes: paths are
workspace-relative and resolved through ``safe_workspace_path``, so a node can
never read or write outside ``GRAPH_WORKFLOW_FILES_DIR``, and generated output
lands in the workspace where the ``file.*`` nodes of fase 4.2 can pick it up.

The two directions are asymmetric on purpose:

* ``audio.transcribe`` and ``image.ocr`` *consume* a file and return text, so
  their output is the text itself — usable straight from an expression.
* ``image.generate`` and ``tts`` *produce* a file, so their output is the path
  they wrote plus the metadata needed to trace which provider made it. Returning
  megabytes of base64 through the run log would be unusable, and would be stored
  in every node_run row.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import uuid

import aiosqlite

from app.core.config import settings
from app.workflow.context import _FILE_MAX_BYTES, _safe_workspace_path
from app.workflow.registry import DispatchCtx, node

#: Generated files land here (inside the workspace root) unless the node says
#: otherwise, so a workflow that does not care about paths still stays tidy.
_GENERATED_DIR = "generated"


def _input_path(params: dict, node_input, key: str = "path") -> str:
    """The node's path param, defaulting to the node input.

    Mirrors doc.convert: chaining a file.watch trigger straight into one of
    these nodes should just work, without restating ``{{ $trigger.path }}``.
    """
    raw = params.get(key)
    if raw in (None, ""):
        if isinstance(node_input, str):
            raw = node_input
        elif isinstance(node_input, dict):
            raw = node_input.get(key) or node_input.get("path")
    if raw in (None, ""):
        raise ValueError(f"'{key}' is required")
    return str(raw)


def _existing_file(raw: str, kind: str):
    path = _safe_workspace_path(raw)
    if not path.is_file():
        raise FileNotFoundError(f"{kind}: {raw!r} not found in the workspace storage")
    if path.stat().st_size > _FILE_MAX_BYTES:
        raise ValueError(f"{kind}: file exceeds the {_FILE_MAX_BYTES // (1024 * 1024)} MB limit")
    return path


def _output_path(params: dict, extension: str):
    """Resolve where a generated file goes, inventing a name when unspecified."""
    raw = (params.get("path") or "").strip()
    if not raw:
        raw = f"{_GENERATED_DIR}/{uuid.uuid4().hex}{extension}"
    return _safe_workspace_path(raw, create_dirs=True), raw


# ── audio.transcribe ─────────────────────────────────────────────────────────

async def _exec_audio_transcribe(params: dict, node_input) -> dict:
    from app.services import speech_service

    raw = _input_path(params, node_input)
    path = _existing_file(raw, "audio.transcribe")
    result = await speech_service.transcribe(
        str(path),
        model=(params.get("model") or None),
        language=(params.get("language") or None),
    )
    return {"path": raw, **result}


# ── image.ocr ────────────────────────────────────────────────────────────────

_OCR_PROMPT = (
    "Transcribe every piece of text visible in this image, preserving reading "
    "order and line breaks. Reply with the text only — no commentary, no "
    "description of the image. If there is no legible text, reply with nothing."
)


async def _exec_image_ocr(db: aiosqlite.Connection, profile_id: str, params: dict, node_input) -> dict:
    """Read the text out of an image with a vision model.

    There is no OCR engine in the image (tesseract would be another ~100 MB for
    a job the chat models already do, usually better on screenshots and
    handwriting), so this goes through the provider layer like every other LLM
    node.
    """
    from app.schemas.chat import ChatCompletionRequest, ChatMessage
    from app.workflow.nodes.llm import _cached_complete, _extract_usage

    raw = _input_path(params, node_input)
    path = _existing_file(raw, "image.ocr")
    model = (params.get("model") or settings.vision_ocr_model or settings.default_model).strip()

    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    data = await asyncio.to_thread(path.read_bytes)
    data_uri = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    prompt = (params.get("prompt") or "").strip() or _OCR_PROMPT
    message = ChatMessage(
        role="user",
        content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ],
    )
    request = ChatCompletionRequest(
        model=model, messages=[message], stream=False, profile_id=profile_id
    )
    response, cache_status = await _cached_complete(request)
    choices = response.get("choices") or []
    text = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
    return {
        "path": raw,
        "text": text.strip(),
        "chars": len(text.strip()),
        "model": model,
        "_usage": _extract_usage(response),
        "_cache": cache_status,
    }


# ── image.generate ───────────────────────────────────────────────────────────

async def _exec_image_generate(params: dict, node_input) -> dict:
    from app.services import image_service

    prompt = params.get("prompt")
    if prompt in (None, "") and isinstance(node_input, str):
        prompt = node_input
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("image.generate: 'prompt' is required")

    def _size(key: str, default: int) -> int:
        try:
            return max(64, min(int(params.get(key) or default), 2048))
        except (TypeError, ValueError):
            return default

    try:
        result = await image_service.generate_image(
            prompt,
            width=_size("width", 1024),
            height=_size("height", 1024),
            provider=(params.get("provider") or None),
        )
    except image_service.ImageGenerationError as exc:
        raise RuntimeError(f"image.generate: {exc}") from exc

    b64 = result.get("b64_json") or ""
    if not b64:
        raise RuntimeError("image.generate: the provider returned no image data")
    data = base64.b64decode(b64)
    path, raw = _output_path(params, ".png")
    await asyncio.to_thread(path.write_bytes, data)
    return {
        "path": raw,
        "bytes": len(data),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "prompt": prompt,
    }


# ── tts ──────────────────────────────────────────────────────────────────────

async def _exec_tts(params: dict, node_input) -> dict:
    from app.services import speech_service

    text = params.get("text")
    if text in (None, "") and isinstance(node_input, str):
        text = node_input
    text = str(text or "").strip()
    if not text:
        raise ValueError("tts: 'text' is required")

    fmt = (params.get("format") or "mp3").strip().lower()
    if fmt not in ("mp3", "opus", "aac", "flac", "wav", "pcm"):
        raise ValueError(f"tts: unsupported format {fmt!r}")

    try:
        audio = await speech_service.synthesize(
            text,
            model=(params.get("model") or None),
            voice=(params.get("voice") or None),
            response_format=fmt,
        )
    except speech_service.SpeechError as exc:
        raise RuntimeError(f"tts: {exc}") from exc

    path, raw = _output_path(params, f".{fmt}")
    await asyncio.to_thread(path.write_bytes, audio)
    return {
        "path": raw,
        "bytes": len(audio),
        "format": fmt,
        "voice": (params.get("voice") or settings.speech_tts_voice),
        "model": (params.get("model") or settings.speech_tts_model),
        "chars": len(text),
    }


# ── handlers ─────────────────────────────────────────────────────────────────

@node("audio.transcribe")
async def _h_audio_transcribe(c: DispatchCtx):
    return await _exec_audio_transcribe(c.params, c.node_input), ["main"]


@node("image.ocr")
async def _h_image_ocr(c: DispatchCtx):
    return await _exec_image_ocr(c.db, c.profile_id, c.params, c.node_input), ["main"]


@node("image.generate")
async def _h_image_generate(c: DispatchCtx):
    return await _exec_image_generate(c.params, c.node_input), ["main"]


@node("tts")
async def _h_tts(c: DispatchCtx):
    return await _exec_tts(c.params, c.node_input), ["main"]
