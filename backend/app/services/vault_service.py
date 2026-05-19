"""
VaultService — symmetric encryption for API keys stored in SQLite.

Keys are encrypted with Fernet (AES-128-CBC + HMAC-SHA256).  The master secret
is derived from VAULT_SECRET_KEY (settings.vault_secret_key) via SHA-256 so any
arbitrary string works as the env var value.

An in-memory cache avoids a DB round-trip on every request.  The cache is
populated at startup (load_all → warm_cache) and updated on every PUT /key call.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# provider_id → plaintext key
_cache: dict[str, str] = {}


def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str, secret: str) -> str:
    return _fernet(secret).encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str, secret: str) -> str | None:
    try:
        return _fernet(secret).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        logger.warning("vault: failed to decrypt entry — wrong secret or corrupted data")
        return None


def warm_cache(keys: dict[str, str]) -> None:
    _cache.update(keys)


def get(provider_id: str) -> str | None:
    return _cache.get(provider_id)


def put(provider_id: str, plaintext: str) -> None:
    _cache[provider_id] = plaintext


def evict(provider_id: str) -> None:
    _cache.pop(provider_id, None)
