"""
Speech services — transcription (STT) and synthesis (TTS).

Roadmap fase 15.5. Deliberately shaped like ``image_service``: a standalone
service with its own configurable model, calling the provider SDK directly,
rather than a new pair of methods on ``BaseProvider``. Speech is not a chat
completion — it takes a file and returns a file — so threading it through the
ten chat providers would have meant nine implementations raising
NotImplementedError to serve one that does not.

Both entry points go through litellm, which is already the default chat
backend, so the same API keys and the same ``<provider>/<model>`` naming apply
(``whisper-1``, ``groq/whisper-large-v3``, ``openai/gpt-4o-mini-tts``, …).
"""

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class SpeechError(Exception):
    """Transcription or synthesis failed, with a message meant for the user."""


async def transcribe(
    path: str,
    *,
    model: str | None = None,
    language: str | None = None,
) -> dict:
    """Transcribe an audio file. Returns {text, segments, model, language}.

    ``segments`` is best-effort: it is present only when the provider returns
    verbose output. Callers must treat it as optional rather than assume the
    timeline is always there.
    """
    model = (model or settings.speech_transcription_model or "").strip()
    if not model:
        raise SpeechError("no transcription model configured (SPEECH_TRANSCRIPTION_MODEL)")

    try:
        import litellm  # noqa: PLC0415 — optional at import time, required here
    except ImportError:  # pragma: no cover — litellm ships in the image
        raise SpeechError("the 'litellm' package is required for transcription") from None

    kwargs: dict = {"model": model, "response_format": "verbose_json"}
    if language:
        kwargs["language"] = language

    try:
        with open(path, "rb") as handle:
            response = await litellm.atranscription(file=handle, **kwargs)
    except OSError as exc:
        raise SpeechError(f"cannot read audio file: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — provider errors are many and untyped
        logger.warning("transcription failed with model=%s: %s", model, exc)
        raise SpeechError(f"transcription failed: {exc}") from exc

    data = response.model_dump() if hasattr(response, "model_dump") else dict(response or {})
    return {
        "text": (data.get("text") or "").strip(),
        "segments": data.get("segments") or [],
        "language": data.get("language") or language,
        "model": model,
        "duration": data.get("duration"),
    }


async def synthesize(
    text: str,
    *,
    model: str | None = None,
    voice: str | None = None,
    response_format: str = "mp3",
) -> bytes:
    """Synthesise speech from text. Returns the raw audio bytes."""
    if not (text or "").strip():
        raise SpeechError("no text to synthesise")
    model = (model or settings.speech_tts_model or "").strip()
    if not model:
        raise SpeechError("no TTS model configured (SPEECH_TTS_MODEL)")
    voice = (voice or settings.speech_tts_voice or "alloy").strip()

    try:
        import litellm  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        raise SpeechError("the 'litellm' package is required for speech synthesis") from None

    try:
        response = await litellm.aspeech(
            model=model, voice=voice, input=text, response_format=response_format,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("speech synthesis failed with model=%s: %s", model, exc)
        raise SpeechError(f"speech synthesis failed: {exc}") from exc

    # litellm mirrors the OpenAI SDK response object, whose payload lives under
    # a couple of different attributes depending on the version.
    for attr in ("content", "audio", "read"):
        value = getattr(response, attr, None)
        if callable(value):
            value = value()
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
    if isinstance(response, (bytes, bytearray)):
        return bytes(response)
    raise SpeechError("speech synthesis returned no audio data")
