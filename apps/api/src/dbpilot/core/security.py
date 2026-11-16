"""Password hashing, dashboard sessions and confirmation tokens."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# A real hash of a random password so unknown emails cost the same time as known ones.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

SESSION_AUDIENCE = "dbpilot-dashboard"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < 10:
        problems.append("at least 10 characters")
    if password.lower() == password or password.upper() == password:
        problems.append("upper and lower case letters")
    if not any(c.isdigit() for c in password):
        problems.append("a digit")
    return problems


@dataclass(frozen=True, slots=True)
class SessionClaims:
    user_id: uuid.UUID
    role: str
    session_version: int
    jti: str = ""
    expires_at: datetime | None = None


def issue_session(
    user_id: uuid.UUID, role: str, session_version: int, secret: str, ttl_hours: int
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "sv": session_version,
        "aud": SESSION_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=ttl_hours),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_session(token: str, secret: str) -> SessionClaims | None:
    try:
        data = jwt.decode(token, secret, algorithms=["HS256"], audience=SESSION_AUDIENCE)
        return SessionClaims(
            uuid.UUID(data["sub"]),
            str(data["role"]),
            int(data.get("sv", 0)),
            str(data.get("jti", "")),
            datetime.fromtimestamp(int(data["exp"]), UTC),
        )
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def new_confirmation_token() -> tuple[str, str]:
    """A one-time confirmation token and its hash. Only the hash is stored, so a database
    reader cannot confirm someone else's pending write."""
    token = secrets.token_urlsafe(24)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
