"""Database engine and session helpers.

Client isolation is enforced by Postgres Row-Level Security. Every session carries a data
scope that is re-applied at the start of *every* transaction (``after_begin`` hook), so a
commit in the middle of a unit of work can never drop the scope:

* ``scoped_session(client_id=None)`` - agent/system work. Sees global rows
  (client_id IS NULL) plus, when given, the rows of exactly one client.
* ``scoped_session(owner=True)`` - the human owner's dashboard. Sees every client.

The values are set with ``set_config(..., is_local => true)`` so they never outlive the
transaction or leak through the connection pool.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (used by tests that switch databases)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@event.listens_for(Session, "after_begin")
def _apply_scope_on_begin(session: Session, transaction, connection) -> None:
    scope = session.info.get("scope")
    if scope is None:
        # Fail closed: no scope means global rows only.
        scope = (None, False)
    client_id, owner = scope
    connection.execute(
        text("SELECT set_config('app.bypass_rls', :b, true), set_config('app.client_id', :c, true)"),
        {"b": "on" if owner else "off", "c": str(client_id) if client_id else ""},
    )


def make_session(*, client_id: UUID | None = None, owner: bool = False) -> Session:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    session.info["scope"] = (client_id, owner)
    return session


@contextmanager
def scoped_session(*, client_id: UUID | None = None, owner: bool = False) -> Iterator[Session]:
    session = make_session(client_id=client_id, owner=owner)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
