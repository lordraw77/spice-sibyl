"""
wiki_service — build a per-document section tree ("wiki") from Markdown headings.

Reuses rag_service.parse_markdown_sections so the wiki, the chunker and the
graph all agree on section boundaries and offsets. Summaries are extractive in
Phase 1 (first prose line of each section); Phase 2 may replace them with LLM
summaries over the same rows.
"""

import logging
import time
import uuid

import aiosqlite

from app.schemas.knowledge import WikiPage

logger = logging.getLogger(__name__)

_SUMMARY_CHARS = 240


def _extractive_summary(body: str) -> str:
    for line in body.splitlines():
        s = line.strip(" \t*_`#-")
        if len(s) >= 30:
            return s[:_SUMMARY_CHARS]
    return body.strip()[:_SUMMARY_CHARS]


async def _section_summary(heading: str, body: str) -> str:
    """LLM section summary when WIKI_LLM_SUMMARY is on, else extractive (Phase 28.d).

    Degrades to the extractive summary on any error or for trivially short bodies.
    """
    from app.core.config import settings

    extractive = _extractive_summary(body)
    if not settings.wiki_llm_summary or len(body.strip()) < 200:
        return extractive
    try:
        from app.services import graphrag_service

        model = graphrag_service._model(
            settings.wiki_summary_model,
            settings.graph_community_model,
            settings.graph_extract_model,
        )
        prompt = (
            "Summarise the following documentation section in 1–2 factual sentences. "
            "Output only the summary.\n\n"
            f"Section: {heading}\n{body[:4000]}"
        )
        text = (await graphrag_service._llm(prompt, model, max_tokens=160)).strip()
        return text or extractive
    except Exception as exc:  # noqa: BLE001 — summaries are best-effort
        logger.warning("Wiki LLM summary failed for %r (%s); using extractive", heading, exc)
        return extractive


async def build_wiki(
    db: aiosqlite.Connection, document_id: str, profile_id: str, markdown: str
) -> None:
    """Persist the document's heading tree into kb_wiki_pages.

    Parent links are derived from heading levels (a section's parent is the most
    recent shallower heading). Best-effort: logs and swallows failures so wiki
    build never fails ingestion.
    """
    from app.services.rag_service import parse_markdown_sections

    try:
        sections = [s for s in parse_markdown_sections(markdown) if s.heading]
        now = int(time.time())
        rows = []
        # Stack of (level, page_id) to resolve parents by heading depth.
        stack: list[tuple[int, str]] = []
        for ordinal, sec in enumerate(sections):
            page_id = str(uuid.uuid4())
            while stack and stack[-1][0] >= sec.level:
                stack.pop()
            parent_id = stack[-1][1] if stack else None
            stack.append((sec.level, page_id))
            summary = await _section_summary(sec.heading, sec.body)
            rows.append(
                (
                    page_id, profile_id, document_id, parent_id, sec.heading, sec.level,
                    sec.body_start, sec.body_end, summary, ordinal, now,
                )
            )
        if rows:
            await db.executemany(
                "INSERT INTO kb_wiki_pages (id, profile_id, document_id, parent_id, heading, "
                "level, char_start, char_end, summary, ord, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            await db.commit()
        logger.info("Wiki built for doc %s: %d pages", document_id, len(rows))
    except Exception:
        logger.exception("Wiki build failed for doc %s (continuing without wiki)", document_id)


async def get_wiki(
    db: aiosqlite.Connection, document_id: str
) -> list[WikiPage]:
    """Return the document's wiki pages in document order."""
    async with db.execute(
        "SELECT id, document_id, parent_id, heading, level, char_start, char_end, summary, ord "
        "FROM kb_wiki_pages WHERE document_id = ? ORDER BY ord",
        (document_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        WikiPage(
            id=r["id"],
            document_id=r["document_id"],
            parent_id=r["parent_id"],
            heading=r["heading"],
            level=r["level"],
            char_start=r["char_start"],
            char_end=r["char_end"],
            summary=r["summary"],
            ord=r["ord"],
        )
        for r in rows
    ]
