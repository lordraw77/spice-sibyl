"""/imagine plus the photo, voice and document handlers.

Extracted from the former single-file bot.py.
"""

import asyncio
import base64
import io
import logging
import time

import aiosqlite
import httpx
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from app.core.config import settings
from app.telegram.i18n import t
from app.dependencies.provider_factory import get_provider
from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.services.image_service import (
    generate_image,
    ImageGenerationError,
    get_available_provider,
)

from .account import _linked_profile_id
from .conversations import _ensure_hydrated, _persist_exchange
from .state import (
    _MAX_HISTORY,
    _default_model,
    _is_allowed,
    _locale,
    _models,
    _sessions,
    _split,
    counters,
)
from .streaming import _stream_reply

logger = logging.getLogger(__name__)

# ── /imagine command ─────────────────────────────────────────────────────────

async def cmd_imagine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate an image from a text prompt: /imagine <prompt>"""

    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_imagine: accesso negato user_id=%s", user.id)
        return

    prompt = ' '.join(context.args).strip() if context.args else ''
    if not prompt:
        await update.message.reply_text("Uso: /imagine <descrizione dell'immagine>")
        return

    chat_id = update.effective_chat.id
    logger.info("cmd_imagine: chat_id=%s prompt=%r", chat_id, prompt[:80])

    if not get_available_provider():
        await update.message.reply_text("⚠ Nessun provider di generazione immagini configurato.")
        return

    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    sent = await update.message.reply_text("🎨 Generazione in corso…")

    try:
        result = await generate_image(prompt=prompt)
        image_bytes = base64.b64decode(result["b64_json"])
        await update.message.reply_photo(
            photo=io.BytesIO(image_bytes),
            caption=f"🎨 {prompt[:200]}\n\n<i>{result['provider']} · {result['model']}</i>",
            parse_mode=ParseMode.HTML,
        )
        await sent.delete()
        counters.sent += 1
        logger.info("cmd_imagine: immagine generata chat_id=%s provider=%s", chat_id, result["provider"])
    except ImageGenerationError as exc:
        counters.errors += 1
        logger.warning("cmd_imagine: errore generazione chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text(f"⚠ Errore: {exc}")
        except Exception:
            pass
    except Exception as exc:
        counters.errors += 1
        logger.exception("cmd_imagine: errore imprevisto chat_id=%s", chat_id)
        try:
            await sent.edit_text(f"⚠ Errore: {exc}")
        except Exception:
            pass


# ── Photo handler (image-to-text / vision) ──────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photos sent to the bot — use a vision-capable model to describe them."""

    if not update.message or not update.message.photo:
        return
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("handle_photo: accesso negato user_id=%s", user.id)
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    counters.received += 1
    chat_id = update.effective_chat.id
    caption = (update.message.caption or "").strip() or "Descrivi questa immagine in dettaglio."
    model = _models.get(chat_id, _default_model())
    logger.info("handle_photo: chat_id=%s model=%s caption=%r", chat_id, model, caption[:60])

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    sent = await update.message.reply_text("⏳")

    # Download the highest-resolution photo
    photo = update.message.photo[-1]
    try:
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        b64_data = base64.b64encode(photo_bytes).decode()
        data_url = f"data:image/jpeg;base64,{b64_data}"
    except Exception as exc:
        counters.errors += 1
        logger.error("handle_photo: download foto fallito chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text("⚠ Impossibile scaricare la foto.")
        except Exception:
            pass
        return

    # Build multimodal message content
    vision_content = [
        {"type": "text", "text": caption},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]

    await _ensure_hydrated(chat_id)
    session = _sessions[chat_id]
    session.append({"role": "user", "content": vision_content})

    if len(session) > _MAX_HISTORY:
        _sessions[chat_id] = session[-_MAX_HISTORY:]
        session = _sessions[chat_id]

    provider = get_provider(model)
    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in session]
    request = ChatCompletionRequest(model=model, messages=messages, max_tokens=2048)

    full_content = ""
    last_edit = time.monotonic()

    try:
        async for chunk in provider.stream(request):
            if chunk.get("object") == "chat.completion.meta":
                break
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = (choices[0].get("delta") or {}).get("content") or ""
            if not delta:
                continue
            full_content += delta
            now = time.monotonic()
            if now - last_edit >= 1.0:
                try:
                    await sent.edit_text(full_content + " ▌")
                    last_edit = now
                except Exception:
                    pass

        chunks = _split(full_content or "⚠ Nessuna risposta.")
        await sent.edit_text(chunks[0])
        for extra in chunks[1:]:
            await update.message.reply_text(extra)

        if full_content:
            session.append({"role": "assistant", "content": full_content})
            counters.sent += 1
            logger.info("handle_photo: risposta completata chat_id=%s response_len=%d", chat_id, len(full_content))
            # Phase 23.a: persist the image exchange into the linked profile's conversation
            await _persist_exchange(chat_id, model, vision_content, full_content)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        counters.errors += 1
        logger.exception("handle_photo: errore chat_id=%s model=%s", chat_id, model)
        try:
            await sent.edit_text(f"⚠ Errore: {exc}")
        except Exception:
            pass
        if session and session[-1]["role"] == "user":
            session.pop()


# ── Voice / audio handler ───────────────────────────────────────────────────

async def _transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe audio via Groq Whisper API. Falls back with a clear error."""
    api_key = settings.groq_api_key
    if not api_key:
        raise RuntimeError("GROQ_API_KEY non configurata — impossibile trascrivere l'audio")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filename, audio_bytes, "audio/ogg")},
            data={"model": "whisper-large-v3", "language": "it"},
        )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice/audio messages: transcribe via Whisper, then process as text."""

    msg = update.message
    if not msg:
        return
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("handle_voice: accesso negato user_id=%s", user.id)
        await msg.reply_text("⛔ Accesso non autorizzato.")
        return

    counters.received += 1
    chat_id = update.effective_chat.id
    logger.info("handle_voice: chat_id=%s user_id=%s", chat_id, user.id)

    voice = msg.voice or msg.audio
    if not voice:
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    sent = await msg.reply_text("🎙️ Trascrizione in corso…")

    try:
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = await file.download_as_bytearray()
    except Exception as exc:
        counters.errors += 1
        logger.error("handle_voice: download audio fallito chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text("⚠ Impossibile scaricare l'audio.")
        except Exception:
            pass
        return

    try:
        transcript = await _transcribe_audio(bytes(audio_bytes))
    except Exception as exc:
        counters.errors += 1
        logger.error("handle_voice: trascrizione fallita chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text(f"⚠ Trascrizione fallita: {exc}")
        except Exception:
            pass
        return

    if not transcript:
        try:
            await sent.edit_text("⚠ Nessun testo riconosciuto nell'audio.")
        except Exception:
            pass
        return

    logger.info("handle_voice: trascritto chat_id=%s len=%d", chat_id, len(transcript))
    try:
        await sent.edit_text(f"🎙️ <i>{transcript}</i>", parse_mode=ParseMode.HTML)
    except Exception:
        pass

    model = _models.get(chat_id, _default_model())
    await _ensure_hydrated(chat_id)
    session = _sessions[chat_id]
    session.append({"role": "user", "content": transcript})
    if len(session) > _MAX_HISTORY:
        _sessions[chat_id] = session[-_MAX_HISTORY:]
        session = _sessions[chat_id]

    reply_sent = await msg.reply_text("⏳")
    await _stream_reply(chat_id, session, model, reply_sent, update, persist_user=transcript)


# ── Document handler (PDF, TXT, DOCX) ──────────────────────────────────────

_SUPPORTED_MIME = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

_SUPPORTED_EXT = (".pdf", ".txt", ".md", ".markdown", ".docx")

_MAX_DOC_CHARS = 8000


async def _ingest_document_to_kb(update: Update, context: ContextTypes.DEFAULT_TYPE, doc, fname: str) -> None:
    """Phase 21: ingest a /kb-captioned document into the linked profile's KB.

    Reuses rag_service.ingest — the same extraction/chunking/embedding as web
    uploads — with byte-hash duplicate detection. Prompts for /link when the
    Telegram user has no linked web profile (21.c).
    """

    msg = update.message
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)

    profile_id = await _linked_profile_id(update.effective_user.id)
    if not profile_id:
        await msg.reply_text(t(loc, "kb_not_linked"))
        return

    sent = await msg.reply_text(t(loc, "kb_ingesting"), parse_mode=ParseMode.HTML)
    try:
        file = await context.bot.get_file(doc.file_id)
        data = bytes(await file.download_as_bytearray())
    except Exception as exc:
        counters.errors += 1
        logger.error("kb_ingest: download fallito chat_id=%s: %s", chat_id, exc)
        await sent.edit_text("⚠ Impossibile scaricare il file.")
        return

    import hashlib
    from app.db import kb_repository
    from app.services import rag_service

    content_hash = hashlib.sha256(data).hexdigest()
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            existing = await kb_repository.find_by_hash(db, profile_id, content_hash)
            if existing:
                await sent.edit_text(
                    t(loc, "kb_duplicate", filename=existing.filename), parse_mode=ParseMode.HTML
                )
                return
            doc_id = await kb_repository.create_document(
                db, profile_id, fname, doc.mime_type, doc.file_size, content_hash=content_hash
            )
            chunk_count = await rag_service.ingest(db, doc_id, profile_id, fname, data)
        logger.info(
            "kb_ingest: OK chat_id=%s profile=%s file=%r doc_id=%s chunks=%d",
            chat_id, profile_id, fname, doc_id, chunk_count,
        )
        await sent.edit_text(
            t(loc, "kb_ingested", filename=fname, chunks=chunk_count), parse_mode=ParseMode.HTML
        )
        # Phase 23.c: surface the ingestion as a web toast/badge for the linked profile.
        try:
            from app.db.database import get_db
            from app.services import notification_service
            async for wdb in get_db():
                await notification_service.notify_web(
                    wdb, profile_id, "kbIngested",
                    title="📄 Documento aggiunto alla KB", body=fname,
                )
        except Exception:
            logger.exception("kb_ingest: notify_web failed chat_id=%s file=%r", chat_id, fname)
    except Exception as exc:
        counters.errors += 1
        logger.warning("kb_ingest: FAILED chat_id=%s file=%r — %s", chat_id, fname, exc)
        await sent.edit_text(t(loc, "kb_ingest_failed", error=str(exc)), parse_mode=ParseMode.HTML)


def _extract_text_from_pdf(data: bytes) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            pages.append(t)
    return "\n\n".join(pages)


def _extract_text_from_docx(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_text_from_txt(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle PDF, TXT, DOCX documents: extract text and send as context to the model."""

    msg = update.message
    if not msg or not msg.document:
        return
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("handle_document: accesso negato user_id=%s", user.id)
        await msg.reply_text("⛔ Accesso non autorizzato.")
        return

    doc = msg.document
    mime = doc.mime_type or ""
    fname = doc.file_name or "file"

    # Accept by MIME or by extension (Telegram tags .md/.markdown inconsistently).
    if mime not in _SUPPORTED_MIME and not fname.lower().endswith(_SUPPORTED_EXT):
        await msg.reply_text(
            f"⚠ Formato non supportato: <code>{mime}</code>\n"
            f"Formati accettati: PDF, TXT, DOCX, MD",
            parse_mode=ParseMode.HTML,
        )
        return

    counters.received += 1
    chat_id = update.effective_chat.id
    raw_caption = (msg.caption or "").strip()

    # Phase 21: a /kb caption ingests the file into the linked profile's KB
    # instead of the one-shot context path.
    if raw_caption.lower().split()[:1] == ["/kb"]:
        await _ingest_document_to_kb(update, context, doc, fname)
        return

    caption = raw_caption or "Analizza il contenuto di questo documento."
    model = _models.get(chat_id, _default_model())
    logger.info("handle_document: chat_id=%s file=%s mime=%s model=%s", chat_id, fname, mime, model)

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    sent = await msg.reply_text("📄 Estrazione testo…")

    try:
        file = await context.bot.get_file(doc.file_id)
        file_bytes = await file.download_as_bytearray()
    except Exception as exc:
        counters.errors += 1
        logger.error("handle_document: download fallito chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text("⚠ Impossibile scaricare il file.")
        except Exception:
            pass
        return

    try:
        if mime == "application/pdf":
            extracted = _extract_text_from_pdf(bytes(file_bytes))
        elif mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            extracted = _extract_text_from_docx(bytes(file_bytes))
        else:
            extracted = _extract_text_from_txt(bytes(file_bytes))
    except Exception as exc:
        counters.errors += 1
        logger.error("handle_document: estrazione fallita chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text(f"⚠ Errore nell'estrazione del testo: {exc}")
        except Exception:
            pass
        return

    if not extracted.strip():
        try:
            await sent.edit_text("⚠ Nessun testo estraibile dal documento.")
        except Exception:
            pass
        return

    truncated = ""
    if len(extracted) > _MAX_DOC_CHARS:
        extracted = extracted[:_MAX_DOC_CHARS]
        truncated = f"\n\n[Documento troncato a {_MAX_DOC_CHARS} caratteri]"

    doc_context = f"📄 **{fname}**\n\n{extracted}{truncated}"

    try:
        await sent.edit_text(
            f"📄 Testo estratto ({len(extracted)} caratteri). Elaborazione in corso…"
        )
    except Exception:
        pass

    await _ensure_hydrated(chat_id)
    session = _sessions[chat_id]
    doc_message = f"{caption}\n\n{doc_context}"
    session.append({"role": "user", "content": doc_message})
    if len(session) > _MAX_HISTORY:
        _sessions[chat_id] = session[-_MAX_HISTORY:]
        session = _sessions[chat_id]

    reply_sent = await msg.reply_text("⏳")
    await _stream_reply(chat_id, session, model, reply_sent, update, persist_user=doc_message)
