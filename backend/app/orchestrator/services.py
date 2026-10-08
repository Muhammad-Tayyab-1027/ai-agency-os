"""Owner-facing actions that start Manager work: goals and chat messages."""

from decimal import Decimal

from sqlalchemy import select

from app.agents.registry import get_manager
from app.core.audit import audit
from app.db.models import AgentMessage, ChatMessage, Goal, Task
from app.orchestrator import lifecycle


class ServiceError(Exception):
    pass


def create_goal(session, *, title: str, target_revenue: Decimal, currency: str, period: str,
                priority_services: list[str], notes: str | None, actor: str) -> tuple[Goal, Task]:
    manager = get_manager(session)
    if manager is None:
        raise ServiceError("No active Manager agent. Run the setup command first.")
    goal = Goal(title=title, target_revenue=target_revenue, currency=currency.upper(), period=period,
                priority_services=priority_services, notes=notes)
    session.add(goal)
    session.flush()
    task = lifecycle.create_task(
        session,
        title=f"Plan goal: {title}",
        description="Turn this revenue goal into a strategy, the agents needed and the first concrete tasks.",
        agent=manager, requested_by=actor, kind="plan", goal_id=goal.id, priority=1,
        review_required=False, enforce_limits=False,
    )
    audit(session, actor_type="user", actor=actor, action="goal.created", target_type="goal", target_id=goal.id,
          data={"title": title, "target": str(target_revenue), "period": period})
    return goal, task


def post_owner_message(session, *, text: str, actor: str) -> tuple[ChatMessage, Task]:
    manager = get_manager(session)
    if manager is None:
        raise ServiceError("No active Manager agent. Run the setup command first.")
    msg = ChatMessage(role="owner", author=actor, content=text)
    session.add(msg)
    session.flush()
    task = lifecycle.create_task(
        session, title="Answer the owner", description=text[:500], agent=manager, requested_by=actor,
        kind="chat", inputs={"message": text, "chat_message_id": str(msg.id)}, priority=1,
        review_required=False, enforce_limits=False,
    )
    msg.task_id = task.id
    return msg, task


def create_checkin_if_needed(session) -> Task | None:
    """Batch unprocessed events for the Manager into one check-in task."""
    manager = get_manager(session)
    if manager is None:
        return None
    open_checkin = session.execute(
        select(Task.id).where(Task.kind == "checkin", Task.status.in_(("queued", "running")))
    ).first()
    if open_checkin:
        return None
    events = session.execute(
        select(AgentMessage).where(AgentMessage.type == "event", AgentMessage.to_agent == "manager",
                                   AgentMessage.task_id.is_(None)).order_by(AgentMessage.created_at).limit(50)
    ).scalars().all()
    if not events:
        return None
    task = lifecycle.create_task(
        session, title=f"Check-in: {len(events)} update(s)", description="Review recent events and act if needed.",
        agent=manager, requested_by="system", kind="checkin",
        inputs={"events": [{"at": e.created_at.isoformat(), **e.payload} for e in events]},
        priority=2, review_required=False, enforce_limits=False,
    )
    for e in events:
        e.task_id = task.id
    return task

