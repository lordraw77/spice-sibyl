import time

import aiosqlite

from app.core.config import settings
from app.services import vault_service


async def store_key(db: aiosqlite.Connection, provider_id: str, plaintext: str) -> None:
    encrypted = vault_service.encrypt(plaintext, settings.vault_secret_key)
    await db.execute(
        """INSERT INTO api_keys (provider_id, encrypted_key, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(provider_id) DO UPDATE
             SET encrypted_key = excluded.encrypted_key,
                 updated_at    = excluded.updated_at""",
        (provider_id, encrypted, int(time.time())),
    )
    await db.commit()
    vault_service.put(provider_id, plaintext)


async def delete_key(db: aiosqlite.Connection, provider_id: str) -> None:
    await db.execute("DELETE FROM api_keys WHERE provider_id = ?", (provider_id,))
    await db.commit()
    vault_service.evict(provider_id)


async def load_all(db: aiosqlite.Connection) -> None:
    """Decrypt all stored keys and warm the in-memory cache. Called once at startup."""
    async with db.execute("SELECT provider_id, encrypted_key FROM api_keys") as cursor:
        rows = await cursor.fetchall()
    decrypted: dict[str, str] = {}
    for provider_id, encrypted in rows:
        plain = vault_service.decrypt(encrypted, settings.vault_secret_key)
        if plain:
            decrypted[provider_id] = plain
    vault_service.warm_cache(decrypted)
