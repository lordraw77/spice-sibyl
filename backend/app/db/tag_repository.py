import time
import uuid

import aiosqlite

from app.schemas.tags import Tag


def _row_to_tag(row: aiosqlite.Row) -> Tag:
    return Tag(
        id=row["id"],
        profile_id=row["profile_id"],
        name=row["name"],
        color=row["color"],
        created_at=row["created_at"],
    )


async def list_tags(db: aiosqlite.Connection, profile_id: str) -> list[Tag]:
    async with db.execute(
        "SELECT * FROM tags WHERE profile_id = ? ORDER BY name ASC",
        (profile_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_tag(r) for r in rows]


async def create_tag(
    db: aiosqlite.Connection, name: str, color: str, profile_id: str
) -> Tag:
    tag_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO tags (id, profile_id, name, color, created_at) VALUES (?, ?, ?, ?, ?)",
        (tag_id, profile_id, name, color, now),
    )
    await db.commit()
    return Tag(id=tag_id, profile_id=profile_id, name=name, color=color, created_at=now)


async def update_tag(
    db: aiosqlite.Connection, tag_id: str, name: str | None, color: str | None
) -> Tag | None:
    async with db.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    new_name = name if name is not None else row["name"]
    new_color = color if color is not None else row["color"]
    await db.execute(
        "UPDATE tags SET name = ?, color = ? WHERE id = ?",
        (new_name, new_color, tag_id),
    )
    await db.commit()
    return Tag(
        id=tag_id, profile_id=row["profile_id"], name=new_name,
        color=new_color, created_at=row["created_at"],
    )


async def delete_tag(db: aiosqlite.Connection, tag_id: str) -> None:
    await db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    await db.commit()


async def get_conversation_tags(
    db: aiosqlite.Connection, conversation_id: str
) -> list[Tag]:
    async with db.execute(
        "SELECT t.* FROM tags t JOIN conversation_tags ct ON t.id = ct.tag_id WHERE ct.conversation_id = ?",
        (conversation_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_tag(r) for r in rows]


async def set_conversation_tags(
    db: aiosqlite.Connection, conversation_id: str, tag_ids: list[str]
) -> None:
    await db.execute(
        "DELETE FROM conversation_tags WHERE conversation_id = ?",
        (conversation_id,),
    )
    for tag_id in tag_ids:
        await db.execute(
            "INSERT OR IGNORE INTO conversation_tags (conversation_id, tag_id) VALUES (?, ?)",
            (conversation_id, tag_id),
        )
    await db.commit()


async def get_tags_for_conversations(
    db: aiosqlite.Connection, conversation_ids: list[str]
) -> dict[str, list[Tag]]:
    if not conversation_ids:
        return {}
    placeholders = ",".join(["?"] * len(conversation_ids))
    query = (  # noqa: S608 — placeholders are safe parameterized ?
        "SELECT ct.conversation_id, t.* FROM tags t"
        " JOIN conversation_tags ct ON t.id = ct.tag_id"
        " WHERE ct.conversation_id IN (" + placeholders + ")"
    )
    async with db.execute(query, conversation_ids) as cursor:
        rows = await cursor.fetchall()
    result: dict[str, list[Tag]] = {}
    for row in rows:
        conv_id = row["conversation_id"]
        if conv_id not in result:
            result[conv_id] = []
        result[conv_id].append(_row_to_tag(row))
    return result
