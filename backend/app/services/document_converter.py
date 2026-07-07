"""
document_converter — universal file/bytes → Markdown via Microsoft MarkItDown.

Replaces the old per-format extraction (PyPDF2 / python-docx / regex-HTML) with a
single structure-preserving path: MarkItDown emits Markdown (headings, tables,
lists) which the wikillm pipeline chunks by section and turns into a wiki + graph.

Plugins and LLM-based image/audio description are intentionally disabled in
Phase 1 to keep ingestion cheap and fully offline; they can be enabled later by
constructing MarkItDown with an llm_client.
"""

import logging
import os
import tempfile
from functools import lru_cache

logger = logging.getLogger(__name__)

# Extensions MarkItDown converts that we accept for upload. Plain text/markdown
# pass through (normalised), everything else is transcoded to Markdown.
SUPPORTED_EXTENSIONS = (
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".json", ".xml",
    ".html", ".htm", ".txt", ".md", ".markdown", ".epub",
)


@lru_cache(maxsize=1)
def _converter():
    """Lazily build and cache one MarkItDown instance (import is heavy)."""
    from markitdown import MarkItDown

    return MarkItDown(enable_plugins=False)


def _result_text(result) -> str:
    """Extract Markdown from a MarkItDown result across library versions."""
    text = getattr(result, "text_content", None)
    if text is None:
        text = getattr(result, "markdown", None)
    if text is None:
        text = str(result)
    return (text or "").strip()


def _ext_of(filename: str) -> str:
    name = (filename or "").lower()
    return f".{name.rsplit('.', 1)[-1]}" if "." in name else ""


def to_markdown(filename: str, data: bytes) -> str:
    """Convert uploaded bytes to Markdown, keyed off the filename extension.

    Uses a temp file + MarkItDown.convert() — the most stable API across
    MarkItDown releases. Raises ValueError on unreadable/empty documents so the
    caller can mark the KB document status='error'.
    """
    ext = _ext_of(filename)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        result = _converter().convert(tmp_path)
    except Exception as exc:
        raise ValueError(f"Could not convert {filename!r} to Markdown: {exc}") from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    text = _result_text(result)
    if not text:
        raise ValueError(f"No extractable text found in {filename!r}.")
    return text


def html_to_markdown(html: str) -> str:
    """Convert a raw HTML string (e.g. a fetched web page) to clean Markdown.

    Degrades to the raw string on failure so URL ingestion never hard-fails on a
    conversion hiccup.
    """
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
            tmp.write(html.encode("utf-8", errors="replace"))
            tmp_path = tmp.name
        return _result_text(_converter().convert(tmp_path))
    except Exception as exc:
        logger.warning("HTML→Markdown conversion failed (%s); using raw text", exc)
        return html.strip()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
