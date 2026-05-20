import re

import aiosqlite

from app.schemas.conversations import SearchResult


def _fts_query(q: str) -> str:
    """Convert a user query to an FTS5 prefix-match expression.

    Each word becomes "word"* so 'hello wor' matches 'hello world'.
    Special FTS5 characters are stripped to avoid syntax errors.
    """
    words = re.sub(r'[^\w\s]', ' ', q).split()
    if not words:
        return '""'
    return ' '.join(f'"{w}"*' for w in words)


def _excerpt(content: str, query: str, radius: int = 80) -> str:
    """Return a plain-text excerpt of content around the first match."""
    first_word = re.sub(r'[^\w]', '', query.split()[0]).lower() if query.split() else ''
    pos = content.lower().find(first_word) if first_word else -1
    if pos == -1:
        return content[:radius * 2].strip()
    start = max(0, pos - radius)
    end = min(len(content), pos + radius)
    prefix = '…' if start > 0 else ''
    suffix = '…' if end < len(content) else ''
    return prefix + content[start:end].strip() + suffix


async def search_conversations(
    db: aiosqlite.Connection,
    query: str,
    profile_id: str | None = None,
    limit: int = 20,
) -> list[SearchResult]:
    if not query or not query.strip():
        return []

    fts_q = _fts_query(query.strip())

    profile_filter = "AND c.profile_id = ?" if profile_id else ""
    params = [fts_q] + ([profile_id] if profile_id else []) + [limit]

    async with db.execute(
        f"""
        SELECT
            c.id,
            c.title,
            c.model,
            c.updated_at,
            m_src.content AS content
        FROM messages_fts f
        JOIN messages m_src ON m_src.id = f.id
        JOIN conversations c ON c.id = f.conversation_id
        WHERE messages_fts MATCH ?
        {profile_filter}
        GROUP BY c.id
        ORDER BY c.updated_at DESC
        LIMIT ?
        """,
        params,
    ) as cursor:
        rows = await cursor.fetchall()

    return [
        SearchResult(
            id=r["id"],
            title=r["title"],
            model=r["model"],
            updated_at=r["updated_at"],
            snippet=_excerpt(r["content"], query.strip()),
        )
        for r in rows
    ]
