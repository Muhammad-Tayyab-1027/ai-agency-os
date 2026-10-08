"""Password hashing and session tokens for the human dashboard."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import get_settings

_hasher = PasswordHasher()

# What each human role may do through the API.
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {"read", "operate", "approve", "admin"},
    "operator": {"read", "operate"},
    "viewer": {"read"},
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_session_token(user_id: UUID, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=settings.session_ttl_hours),
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm="HS256")


def decode_session_token(token: str) -> dict:
    return jwt.decode(token, get_settings().secret_key.get_secret_value(), algorithms=["HS256"])
