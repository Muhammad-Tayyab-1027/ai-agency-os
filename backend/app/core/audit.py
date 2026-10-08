"""Append-only, hash-chained audit log.

Each entry stores sha256(prev_hash + canonical_json(entry)). A transaction-scoped advisory
lock serialises writers so the chain stays linear. ``verify_chain`` re-computes every hash
and reports the first broken link, so tampering done outside the app (the table itself
rejects UPDATE/DELETE via trigger) is still detectable.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import AuditLog

GENESIS = "0" * 64
_AUDIT_LOCK_KEY = 72_001_001

_SECRET_KEYS = re.compile(r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie)", re.I)


def redact(value: Any) -> Any:
    """Strip anything that looks like a credential before it is persisted or logged."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)


def _entry_dict(row: AuditLog) -> dict[str, Any]:
    return {
        "ts": row.ts.astimezone(UTC).isoformat(),
        "actor_type": row.actor_type,
        "actor": row.actor,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "client_id": str(row.client_id) if row.client_id else None,
        "outcome": row.outcome,
        "data": row.data,
    }


def _hash(prev_hash: str, entry: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(entry)).encode()).hexdigest()


def audit(
    session: Session,
    *,
    actor_type: str,
    actor: str,
    action: str,
    target_type: str | None = None,
    target_id: Any = None,
    client_id: UUID | None = None,
    outcome: str = "ok",
    data: dict[str, Any] | None = None,
) -> AuditLog:
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})
    prev = session.execute(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)).scalar()
    row = AuditLog(
        ts=datetime.now(UTC),
        actor_type=actor_type,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        client_id=client_id,
        outcome=outcome,
        # Round-trip through JSON so the stored value hashes identically on verification.
        data=json.loads(_canonical(redact(data or {}))),
        prev_hash=prev or GENESIS,
        hash="",
    )
    row.hash = _hash(row.prev_hash, _entry_dict(row))
    session.add(row)
    session.flush()
    return row


def verify_chain(session: Session) -> dict[str, Any]:
    prev = GENESIS
    count = 0
    for row in session.execute(select(AuditLog).order_by(AuditLog.id)).scalars():
        if row.prev_hash != prev or _hash(prev, _entry_dict(row)) != row.hash:
            return {"valid": False, "checked": count, "broken_at_id": row.id}
        prev = row.hash
        count += 1
    return {"valid": True, "checked": count, "broken_at_id": None}
