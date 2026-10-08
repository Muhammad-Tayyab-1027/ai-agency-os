"""Runtime settings (editable from the dashboard), rate limits, budgets and error capture."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ErrorEvent, LlmUsage, RateCounter, Setting

# Settings the owner may change at runtime, with their env-backed defaults.
EDITABLE_SETTINGS = {
    "paused": lambda s: False,  # global kill switch
    "max_agents": lambda s: s.max_agents,
    "daily_llm_budget_usd": lambda s: s.daily_llm_budget_usd,
    "outreach_daily_cap": lambda s: s.outreach_daily_cap,
    "max_open_tasks": lambda s: s.max_open_tasks,
}


def get_setting(session: Session, key: str) -> Any:
    if key not in EDITABLE_SETTINGS:
        raise KeyError(key)
    row = session.get(Setting, key)
    if row is not None:
        return row.value.get("v")
    return EDITABLE_SETTINGS[key](get_settings())


def all_settings(session: Session) -> dict[str, Any]:
    return {k: get_setting(session, k) for k in EDITABLE_SETTINGS}


def set_setting(session: Session, key: str, value: Any) -> None:
    if key not in EDITABLE_SETTINGS:
        raise KeyError(key)
    stmt = insert(Setting).values(key=key, value={"v": value})
    stmt = stmt.on_conflict_do_update(index_elements=[Setting.key], set_={"value": {"v": value}})
    session.execute(stmt)


def is_paused(session: Session) -> bool:
    return bool(get_setting(session, "paused"))


def _day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def consume_rate(session: Session, key: str, limit: int, window: timedelta = timedelta(days=1)) -> bool:
    """Atomically count one use of ``key`` in the current window. False if over the limit."""
    if window == timedelta(days=1):
        start = _day_start()
    else:
        epoch = int(datetime.now(UTC).timestamp())
        start = datetime.fromtimestamp(epoch - epoch % int(window.total_seconds()), UTC)
    stmt = (
        insert(RateCounter)
        .values(key=key, window_start=start, count=1)
        .on_conflict_do_update(
            index_elements=[RateCounter.key, RateCounter.window_start],
            set_={"count": RateCounter.count + 1},
            where=RateCounter.count < limit,
        )
        .returning(RateCounter.count)
    )
    return session.execute(stmt).scalar() is not None


def rate_usage(session: Session, key: str) -> int:
    return (
        session.execute(
            select(RateCounter.count).where(RateCounter.key == key, RateCounter.window_start == _day_start())
        ).scalar()
        or 0
    )


def spent_today(session: Session, agent_id: UUID | None = None) -> float:
    q = select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(LlmUsage.created_at >= _day_start())
    if agent_id is not None:
        q = q.where(LlmUsage.agent_id == agent_id)
    return float(session.execute(q).scalar() or 0.0)


def record_error(
    session: Session,
    source: str,
    message: str,
    *,
    detail: dict | None = None,
    task_id: UUID | None = None,
    agent_id: UUID | None = None,
) -> None:
    session.add(
        ErrorEvent(
            source=source, message=message[:4000], detail=detail or {}, task_id=task_id, agent_id=agent_id
        )
    )
    session.flush()


def db_now(session: Session) -> datetime:
    return session.execute(text("SELECT now()")).scalar()
