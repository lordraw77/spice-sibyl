import time
import uuid

import aiosqlite

from app.schemas.templates import PromptTemplate


def _row_to_template(row: aiosqlite.Row) -> PromptTemplate:
    return PromptTemplate(
        id=row["id"],
        profile_id=row["profile_id"],
        name=row["name"],
        content=row["content"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def list_templates(
    db: aiosqlite.Connection, profile_id: str
) -> list[PromptTemplate]:
    async with db.execute(
        "SELECT * FROM prompt_templates WHERE profile_id = ? ORDER BY updated_at DESC",
        (profile_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_template(r) for r in rows]


async def create_template(
    db: aiosqlite.Connection, name: str, content: str, profile_id: str
) -> PromptTemplate:
    template_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO prompt_templates (id, profile_id, name, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (template_id, profile_id, name, content, now, now),
    )
    await db.commit()
    return PromptTemplate(
        id=template_id, profile_id=profile_id, name=name, content=content,
        created_at=now, updated_at=now,
    )


async def update_template(
    db: aiosqlite.Connection, template_id: str, name: str | None, content: str | None
) -> PromptTemplate | None:
    async with db.execute(
        "SELECT * FROM prompt_templates WHERE id = ?", (template_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    now = int(time.time())
    new_name = name if name is not None else row["name"]
    new_content = content if content is not None else row["content"]
    await db.execute(
        "UPDATE prompt_templates SET name = ?, content = ?, updated_at = ? WHERE id = ?",
        (new_name, new_content, now, template_id),
    )
    await db.commit()
    return PromptTemplate(
        id=template_id, profile_id=row["profile_id"], name=new_name,
        content=new_content, created_at=row["created_at"], updated_at=now,
    )


async def delete_template(db: aiosqlite.Connection, template_id: str) -> None:
    await db.execute("DELETE FROM prompt_templates WHERE id = ?", (template_id,))
    await db.commit()
