"""Owner reports. Numbers are computed from the database (never by the LLM); the Manager then
writes the narrative to the owner in its own voice, and the report is posted into the chat."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.agents.registry import agent_metrics, get_manager
from app.core.audit import audit
from app.db.models import (
    Agent,
    Approval,
    ChatMessage,
    ErrorEvent,
    Goal,
    HumanTask,
    Lead,
    LlmUsage,
    Report,
    RevenueEvent,
    Task,
)
from app.orchestrator import lifecycle


def compute_report_data(session, start: datetime, end: datetime) -> dict[str, Any]:
    def count(q):
        return session.execute(q).scalar_one()

    goal = session.execute(select(Goal).where(Goal.status == "active").order_by(Goal.created_at.desc())).scalars().first()
    revenue = session.execute(
        select(func.coalesce(func.sum(RevenueEvent.amount), 0)).where(
            RevenueEvent.occurred_at >= start, RevenueEvent.occurred_at < end)
    ).scalar()
    in_period = (Task.updated_at >= start) & (Task.updated_at < end) & (Task.kind == "work")
    metrics = agent_metrics(session)
    agents = session.execute(select(Agent).where(Agent.status != "retired")).scalars().all()
    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "currency": goal.currency if goal else "USD",
        "goal_title": goal.title if goal else None,
        "goal_target": float(goal.target_revenue) if goal else None,
        "goal_period": goal.period if goal else None,
        "revenue": float(revenue or 0),
        "tasks": {
            "completed": count(select(func.count()).select_from(Task).where(in_period, Task.status == "completed")),
            "failed": count(select(func.count()).select_from(Task).where(in_period, Task.status == "failed")),
            "open": count(select(func.count()).select_from(Task).where(
                Task.kind == "work", Task.status.in_(("queued", "running", "in_review", "blocked")))),
            "blocked": count(select(func.count()).select_from(Task).where(Task.kind == "work", Task.status == "blocked")),
        },
        "new_leads": count(select(func.count()).select_from(Lead).where(Lead.created_at >= start, Lead.created_at < end)),
        "leads_by_stage": dict(session.execute(select(Lead.stage, func.count()).group_by(Lead.stage)).all()),
        "pending_approvals": count(select(func.count()).select_from(Approval).where(Approval.status == "pending")),
        "open_human_tasks": count(select(func.count()).select_from(HumanTask).where(HumanTask.status == "open")),
        "errors": count(select(func.count()).select_from(ErrorEvent).where(
            ErrorEvent.created_at >= start, ErrorEvent.created_at < end)),
        "llm_cost_usd": round(float(session.execute(select(func.coalesce(func.sum(LlmUsage.cost_usd), 0)).where(
            LlmUsage.created_at >= start, LlmUsage.created_at < end)).scalar() or 0), 4),
        "failed_tasks": [
            {"title": t.title, "reason": (t.last_error or "")[:300]}
            for t in session.execute(select(Task).where(in_period, Task.status == "failed").limit(10)).scalars()
        ],
        "agents": [
            {"key": a.key, "role": a.template, "status": a.status, **(metrics.get(str(a.id)) or {})}
            for a in agents
        ],
    }


def _data_appendix(data: dict[str, Any]) -> str:
    t = data["tasks"]
    goal = f"{data['goal_target']:,.0f} {data['currency']} {data['goal_period']}" if data["goal_target"] else "not set"
    lines = [
        "---",
        "**Figures (from the database)**",
        "",
        f"- Revenue: {data['revenue']:,.2f} {data['currency']} (goal: {goal})",
        f"- Tasks completed / failed / open: {t['completed']} / {t['failed']} / {t['open']}",
        f"- New leads: {data['new_leads']}",
        f"- Approvals waiting: {data['pending_approvals']}",
        f"- Your to-dos waiting: {data['open_human_tasks']}",
        f"- Errors logged: {data['errors']}",
        f"- AI cost: ${data['llm_cost_usd']:.2f}",
    ]
    return "\n".join(lines)


def request_report(session, kind: str = "daily", *, end: datetime | None = None) -> Task | Report:
    """Create a report task for the Manager (or write a numbers-only report if there is none)."""
    end = end or datetime.now(UTC)
    start = end - (timedelta(days=7) if kind == "weekly" else timedelta(days=1))
    data = compute_report_data(session, start, end)
    manager = get_manager(session)
    if manager is None:
        return _store_report(session, kind, data, "No active Manager - figures only.", task=None)
    return lifecycle.create_task(
        session, title=f"Write the {kind} report for the owner", description="Report to the owner.",
        agent=manager, requested_by="system", kind="report", inputs={"report_kind": kind, **data},
        priority=2, review_required=False, enforce_limits=False,
    )


def finalize_report(session, task: Task, agent: Agent, narrative: str) -> Report:
    data = {k: v for k, v in task.inputs.items() if k != "report_kind"}
    return _store_report(session, task.inputs.get("report_kind", "daily"), data, narrative, task=task, author=agent.name)


def _store_report(session, kind: str, data: dict, narrative: str, *, task: Task | None, author: str = "System") -> Report:
    content = f"{narrative.strip()}\n\n{_data_appendix(data)}"
    report = Report(
        kind=kind,
        period_start=datetime.fromisoformat(data["period_start"]),
        period_end=datetime.fromisoformat(data["period_end"]),
        content=content,
        data=data,
    )
    session.add(report)
    session.flush()
    session.add(ChatMessage(role="manager", author=author, content=content, kind="report", report_id=report.id,
                            task_id=task.id if task else None))
    audit(session, actor_type="system", actor="reports", action="report.created", target_type="report",
          target_id=report.id, data={"kind": kind})
    return report
