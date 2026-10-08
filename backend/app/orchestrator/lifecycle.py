"""Task lifecycle: creation, submission, review, revision, retry, failure and notifications.

Status machine
  queued -> running -> (in_review -> completed | queued[revision] | failed)
                    -> completed            (no review required)
                    -> blocked              (needs the owner / missing capability / failed dependency)
                    -> queued[retry]        (error, attempts left)
                    -> failed               (attempts exhausted; never reported as success)
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.audit import audit
from app.core.runtime import get_setting, record_error
from app.db.enums import TASK_OPEN_STATUSES
from app.db.models import Agent, AgentMessage, Review, Task, TaskDependency
from app.agents.registry import get_manager


class LifecycleError(Exception):
    pass


def now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- messages


def send_message(
    session: Session,
    *,
    type: str,
    from_agent: str,
    to_agent: str,
    task: Task | None,
    payload: dict[str, Any],
    correlation_id: uuid.UUID | None = None,
) -> AgentMessage:
    """Persist one message in the standard inter-agent envelope."""
    msg = AgentMessage(
        correlation_id=correlation_id or (task.id if task else uuid.uuid4()),
        type=type,
        from_agent=from_agent,
        to_agent=to_agent,
        task_id=task.id if task else None,
        client_id=task.client_id if task else None,
        project_id=task.project_id if task else None,
        payload=payload,
    )
    session.add(msg)
    session.flush()
    return msg


def notify_manager(session: Session, text: str, *, about_task: Task | None = None, data: dict | None = None) -> None:
    """Queue an event for the Manager; the scheduler batches events into a check-in task."""
    session.add(
        AgentMessage(
            correlation_id=about_task.id if about_task else uuid.uuid4(),
            type="event",
            from_agent="system",
            to_agent="manager",
            task_id=None,  # set to the check-in task that consumes it
            payload={"text": text, "about_task_id": str(about_task.id) if about_task else None, **(data or {})},
        )
    )
    session.flush()


# --------------------------------------------------------------------------- creation


def open_task_count(session: Session) -> int:
    return session.execute(
        select(func.count()).select_from(Task).where(Task.status.in_(TASK_OPEN_STATUSES), Task.kind == "work")
    ).scalar_one()


def create_task(
    session: Session,
    *,
    title: str,
    description: str = "",
    agent: Agent | None = None,
    required_role: str | None = None,
    requested_by: str = "owner",
    kind: str = "work",
    goal_id: uuid.UUID | None = None,
    parent: Task | None = None,
    inputs: dict | None = None,
    acceptance_criteria: list[str] | None = None,
    priority: int = 3,
    depends_on: list[uuid.UUID] | None = None,
    client_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    review_required: bool | None = None,
    deadline: datetime | None = None,
    enforce_limits: bool = True,
) -> Task:
    settings = get_settings()
    depth = (parent.depth + 1) if parent else 0
    if enforce_limits and kind == "work":
        if depth > settings.max_task_depth:
            raise LifecycleError(f"Task nesting too deep (max {settings.max_task_depth}).")
        limit = int(get_setting(session, "max_open_tasks"))
        if open_task_count(session) >= limit:
            raise LifecycleError(f"Too many open tasks ({limit}). Finish or cancel some first.")

    if review_required is None:
        review_required = kind == "work" and (agent is None or agent.template != "manager")

    task = Task(
        kind=kind,
        title=title[:300],
        description=description,
        goal_id=goal_id or (parent.goal_id if parent else None),
        parent_id=parent.id if parent else None,
        depth=depth,
        assigned_agent_id=agent.id if agent else None,
        required_role=required_role or (agent.template if agent else None),
        requested_by=requested_by,
        status="queued" if agent else "blocked",
        priority=priority,
        inputs=inputs or {},
        acceptance_criteria=acceptance_criteria or [],
        client_id=client_id if client_id is not None else (parent.client_id if parent else None),
        project_id=project_id,
        max_attempts=settings.task_max_attempts,
        max_revisions=settings.task_max_revisions,
        review_required=review_required,
        deadline=deadline,
        last_error=None if agent else f"No active agent with role '{required_role}'.",
    )
    session.add(task)
    session.flush()
    for dep in depends_on or []:
        if session.get(Task, dep) is None:
            raise LifecycleError(f"Dependency task {dep} does not exist.")
        session.add(TaskDependency(task_id=task.id, depends_on_id=dep))
    session.flush()
    send_message(
        session,
        type="task",
        from_agent=requested_by,
        to_agent=agent.key if agent else f"role:{required_role}",
        task=task,
        payload={
            "objective": title,
            "description": description,
            "inputs": inputs or {},
            "acceptance_criteria": acceptance_criteria or [],
            "priority": priority,
            "deadline": deadline.isoformat() if deadline else None,
        },
    )
    audit(
        session, actor_type="agent" if requested_by != "owner" else "user", actor=requested_by,
        action="task.created", target_type="task", target_id=task.id, client_id=task.client_id,
        data={"title": title, "kind": kind, "assigned": agent.key if agent else None},
    )
    return task


def assign_waiting_tasks(session: Session, agent: Agent) -> int:
    """After a new agent is created, hand it the tasks that were blocked waiting for its role."""
    waiting = session.execute(
        select(Task).where(
            Task.status == "blocked", Task.assigned_agent_id.is_(None), Task.required_role == agent.template
        )
    ).scalars().all()
    for task in waiting:
        task.assigned_agent_id = agent.id
        task.status = "queued"
        task.last_error = None
        task.next_run_at = now()
    session.flush()
    return len(waiting)


# --------------------------------------------------------------------------- results


def submit_result(session: Session, task: Task, agent: Agent, result: dict[str, Any]) -> str:
    """Apply an agent's submit_result. Returns the task's new status."""
    status = result.get("status", "success")
    send_message(session, type="response", from_agent=agent.key, to_agent=task.requested_by, task=task,
                 payload=result)
    if status == "failed":
        raise AgentReportedFailure(result.get("summary") or "Agent reported failure.")

    task.output = result
    if status == "needs_human":
        task.status = "blocked"
        task.last_error = "Waiting for the owner: " + (result.get("summary") or "")[:500]
        notify_manager(session, f"Task '{task.title}' is waiting for the owner.", about_task=task)
        return task.status

    if task.review_required and agent.template != "manager":
        task.status = "in_review"
        manager = get_manager(session)
        if manager is None:
            raise LifecycleError("No active Manager to review the result.")
        create_task(
            session,
            title=f"Review: {task.title}",
            description="Review the submitted result against the acceptance criteria and call review_task.",
            agent=manager,
            requested_by="system",
            kind="review",
            parent=None,
            goal_id=task.goal_id,
            inputs={"task_id": str(task.id)},
            priority=max(1, task.priority - 1),
            review_required=False,
            client_id=task.client_id,
            enforce_limits=False,
        )
        return task.status

    complete_task(session, task)
    return task.status


class AgentReportedFailure(Exception):
    pass


def complete_task(session: Session, task: Task) -> None:
    task.status = "completed"
    task.completed_at = now()
    task.locked_by = None
    task.last_error = None
    session.flush()
    audit(session, actor_type="system", actor="orchestrator", action="task.completed", target_type="task",
          target_id=task.id, client_id=task.client_id, data={"title": task.title})


def review(session: Session, task: Task, *, reviewer: str, verdict: str, score: int, feedback: str) -> str:
    if task.status != "in_review":
        raise LifecycleError(f"Task is '{task.status}', not in review.")
    session.add(Review(task_id=task.id, reviewer=reviewer, agent_id=task.assigned_agent_id, verdict=verdict,
                       score=score, feedback=feedback))
    send_message(session, type="review", from_agent=reviewer,
                 to_agent=task.assigned_agent.key if task.assigned_agent else "unknown", task=task,
                 payload={"verdict": verdict, "score": score, "feedback": feedback})
    if verdict == "approve":
        complete_task(session, task)
    elif verdict == "revise":
        if task.revision_count >= task.max_revisions:
            fail_task(session, task, f"Still not acceptable after {task.revision_count} revisions: {feedback}")
        else:
            task.revision_count += 1
            task.feedback = [*task.feedback, {"from": reviewer, "score": score, "feedback": feedback,
                                              "at": now().isoformat()}]
            task.status = "queued"
            task.attempts = 0
            task.next_run_at = now()
    else:
        fail_task(session, task, f"Rejected in review: {feedback}")
    session.flush()
    return task.status


def fail_task(session: Session, task: Task, reason: str) -> None:
    task.status = "failed"
    task.last_error = reason[:4000]
    task.locked_by = None
    record_error(session, "task", f"Task failed: {task.title}: {reason}", task_id=task.id,
                 agent_id=task.assigned_agent_id)
    audit(session, actor_type="system", actor="orchestrator", action="task.failed", target_type="task",
          target_id=task.id, client_id=task.client_id, outcome="error", data={"reason": reason[:1000]})
    # Dependents cannot proceed: block them and tell the Manager.
    dependents = session.execute(
        select(Task).join(TaskDependency, TaskDependency.task_id == Task.id).where(
            TaskDependency.depends_on_id == task.id, Task.status.in_(("queued", "blocked"))
        )
    ).scalars().all()
    for dep in dependents:
        dep.status = "blocked"
        dep.last_error = f"Dependency failed: {task.title}"
    if task.kind == "work":
        notify_manager(session, f"Task FAILED: '{task.title}'. Reason: {reason[:500]}", about_task=task)
    session.flush()


def schedule_retry(session: Session, task: Task, error: str) -> str:
    """Record a failed attempt; retry with backoff or fail permanently."""
    task.last_error = error[:4000]
    task.locked_by = None
    if task.attempts >= task.max_attempts:
        fail_task(session, task, f"Gave up after {task.attempts} attempts. Last error: {error}")
    else:
        task.status = "queued"
        task.next_run_at = now() + timedelta(minutes=2 ** task.attempts)
        record_error(session, "task.retry", f"Attempt {task.attempts} failed for '{task.title}': {error}",
                     task_id=task.id, agent_id=task.assigned_agent_id)
    session.flush()
    return task.status


def resume_blocked_task(session: Session, task: Task, note: str) -> None:
    """Called when the owner finishes a human step the task was waiting for."""
    if task.status != "blocked":
        return
    task.status = "queued" if task.assigned_agent_id else "blocked"
    task.attempts = max(task.attempts - 1, 0)
    task.next_run_at = now()
    task.last_error = None
    task.feedback = [*task.feedback, {"from": "owner", "feedback": f"Owner completed the requested step. {note}",
                                      "at": now().isoformat()}]
    session.flush()


def cancel_task(session: Session, task: Task, actor: str, reason: str) -> None:
    if task.status in ("completed", "failed", "cancelled"):
        raise LifecycleError(f"Task is already {task.status}.")
    task.status = "cancelled"
    task.last_error = f"Cancelled by {actor}: {reason}"
    task.locked_by = None
    session.execute(
        update(Task).where(Task.parent_id == task.id, Task.status.in_(("queued", "blocked"))).values(
            status="cancelled", last_error=f"Parent cancelled: {reason}"
        )
    )
    audit(session, actor_type="user" if actor == "owner" else "agent", actor=actor, action="task.cancelled",
          target_type="task", target_id=task.id, client_id=task.client_id, data={"reason": reason})
    session.flush()
