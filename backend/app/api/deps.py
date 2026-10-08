"""Request dependencies: DB session, authentication, role checks, CSRF guard."""

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import ROLE_PERMISSIONS, decode_session_token
from app.db.models import User
from app.db.session import make_session

SESSION_COOKIE = "aos_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "agency-os"


def get_db() -> Iterator[Session]:
    # Dashboard users are agency staff: they see every client (RLS bypass for this session only).
    session = make_session(owner=True)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass
class CurrentUser:
    id: UUID
    email: str
    role: str

    def can(self, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS.get(self.role, set())


def csrf_guard(request: Request) -> None:
    """Mutating requests must carry a custom header, which browsers never send cross-site
    without a CORS preflight that we do not allow."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header.")


def current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    try:
        claims = decode_session_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Sign in again.") from None
    user = db.get(User, UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled.")
    return CurrentUser(id=user.id, email=user.email, role=user.role)


def require(permission: str):
    def checker(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not user.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your role cannot '{permission}'.")
        return user

    return checker
