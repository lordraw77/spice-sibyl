"""
AuthService — password hashing and JWT issuance/validation for Phase 13.

Passwords are hashed with bcrypt (passlib).  Tokens are signed with HS256 using
settings.jwt_secret_key.  Two token types exist:

  * access  — short-lived (jwt_access_ttl_minutes); carries the user's role so
              authorization checks need no DB round-trip.
  * refresh — long-lived (jwt_refresh_ttl_days); carries a unique ``jti`` that is
              tracked in the ``refresh_tokens`` table so it can be revoked/rotated.
"""

import time
import uuid

import bcrypt
import jwt

from app.core.config import settings

_ALGORITHM = "HS256"


def _encode_secret(plaintext: str) -> bytes:
    # bcrypt only considers the first 72 bytes; truncate explicitly so longer
    # passphrases don't raise on newer bcrypt versions.
    return plaintext.encode("utf-8")[:72]


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(_encode_secret(plaintext), bcrypt.gensalt()).decode("utf-8")


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_encode_secret(plaintext), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed/garbage hash — treat as a failed verification, never raise.
        return False


def _encode(payload: dict) -> str:
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGORITHM)


def create_access_token(user_id: str, role: str) -> str:
    now = int(time.time())
    return _encode(
        {
            "sub": user_id,
            "role": role,
            "type": "access",
            "iat": now,
            "exp": now + settings.jwt_access_ttl_minutes * 60,
        }
    )


def create_refresh_token(user_id: str) -> tuple[str, str, int]:
    """Return (token, jti, expires_at) for a new refresh token."""
    now = int(time.time())
    jti = str(uuid.uuid4())
    expires_at = now + settings.jwt_refresh_ttl_days * 86400
    token = _encode(
        {
            "sub": user_id,
            "jti": jti,
            "type": "refresh",
            "iat": now,
            "exp": expires_at,
        }
    )
    return token, jti, expires_at


def decode_token(token: str) -> dict | None:
    """Decode and verify a token; return claims or None if invalid/expired."""
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None
