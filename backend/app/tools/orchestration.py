"""Manager tools: status, planning, delegation, review and talking to the owner."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import func, select

from app.agents.registry import best_agent_for_role, templates
from app.db.enums import TIER_AUTOMATIC
from app.db.models import (
    Agent,
    Approval,
    ChatMessage,
    Goal,
    HumanTask,
    Lead,
    RevenueEvent,
    Task,
)
from app.orchestrator import lifecycle
from app.tools.base import ToolContext, ToolError, ToolInput, tool


# --------------------------------------------------------------------------- status


class NoInput(ToolInput):
    pass


def agency_snapshot(session) -> dict:
    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    tasks = dict(session.execute(select(Task.status, func.count()).where(Task.kind == "work").group_by(Task.status)).all())
    leads = dict(session.execute(select(Lead.stage, func.count()).group_by(Lead.stage)).all())
    revenue = session.execute(
        select(func.coalesce(func.sum(RevenueEvent.amount), 0)).where(RevenueEvent.occurred_at >= month_start)
    ).scalar()
    goals = [
        {"id": str(g.id), "title": g.title, "target_revenue": float(g.target_revenue), "currency": g.currency,
         "period": g.period, "priority_services": g.priority_services, "has_strategy": bool(g.strategy)}
        for g in session.execute(select(Goal).where(Goal.status == "active")).scalars()
    ]
    agents = [
        {"key": a.key, "role": a.template, "status": a.status}
        for a in session.execute(select(Agent).where(Agent.status != "retired")).scalars()
    ]
    return {
        "goals": goals,
        "revenue_this_month": float(revenue or 0),
        "tasks_by_status": tasks,
        "leads_by_stage": leads,
        "pending_approvals": session.execute(
            select(func.count()).select_from(Approval).where(Approval.status == "pending")
        ).scalar_one(),
        "open_human_tasks": session.execute(
            select(func.count()).select_from(HumanTask).where(HumanTask.status == "open")
        ).scalar_one(),
        "agents": agents,
    }


@tool(
    "get_agency_status",
    description="Current goals, revenue this month, task counts, pipeline counts, pending approvals and agents.",
    input_model=NoInput,
    tier=TIER_AUTOMATIC,
    scopes={"agents:read"},
)
def get_agency_status(ctx: ToolContext, params: NoInput) -> dict:
    return agency_snapshot(ctx.session)


# --------------------------------------------------------------------------- planning


class StrategyInput(ToolInput):
    goal_id: UUID
    summary: str = Field(min_length=20, max_length=4000)
    focus_services: list[str] = Field(min_length=1, max_length=9)
    target_segments: list[str] = Field(min_length=1, max_length=10)
    channels: list[str] = Field(min_length=1, max_length=10)
    weekly_targets: dict[str, int] = Field(
        default_factory=dict, description="e.g. {'leads_researched': 50, 'outreach_sent': 100}"
    )


@tool(
    "set_goal_strategy",
    description="Write or update the strategy for a revenue goal (services, segments, channels, weekly targets).",
    input_model=StrategyInput,
    tier=TIER_AUTOMATIC,
    scopes={"goals:write"},
)
def set_goal_strategy(ctx: ToolContext, p: StrategyInput) -> dict:
    goal = ctx.session.get(Goal, p.goal_id)
    if goal is None:
        raise ToolError("Goal not found.")
    goal.strategy = {**p.model_dump(mode="json", exclude={"goal_id"}), "updated_at": datetime.now(UTC).isoformat()}
    return {"goal_id": str(goal.id), "saved": True}


class CreateTaskInput(ToolInput):
    title: str = Field(min_length=5, max_length=300)
    description: str = Field(min_length=10, max_length=8000)
    role: str = Field(description="Template/role key of the agent that should do it, e.g. 'lead_research'.")
    acceptance_criteria: list[str] = Field(min_length=1, max_length=12)
    inputs: dict = Field(default_factory=dict)
    priority: int = Field(default=3, ge=1, le=5, description="1 = most urgent")
    depends_on: list[UUID] = Field(default_factory=list, description="Task ids that must complete first.")
    goal_id: UUID | None = None
    client_id: UUID | None = None


@tool(
    "create_task",
    description=(
        "Delegate a task to the best active agent with the given role. If no such agent exists the task "
        "is created as blocked; propose an agent for that role to unblock it."
    ),
    input_model=CreateTaskInput,
    tier=TIER_AUTOMATIC,
    scopes={"tasks:create"},
    daily_limit=200,
)
def create_task_tool(ctx: ToolContext, p: CreateTaskInput) -> dict:
    if p.role not in templates():
        raise ToolError(f"Unknown role '{p.role}'. Valid roles: {sorted(templates())}.")
    if p.role == "manager":
        raise ToolError("The Manager cannot delegate tasks to itself.")
    agent = best_agent_for_role(ctx.session, p.role)
    try:
        task = lifecycle.create_task(
            ctx.session,
            title=p.title,
            description=p.description,
            agent=agent,
            required_role=p.role,
            requested_by=ctx.agent.key,
            goal_id=p.goal_id or (ctx.task.goal_id if ctx.task else None),
            parent=ctx.task if ctx.task and ctx.task.kind == "work" else None,
            inputs=p.inputs,
            acceptance_criteria=p.acceptance_criteria,
            priority=p.priority,
            depends_on=p.depends_on,
            client_id=p.client_id,
        )
    except lifecycle.LifecycleError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "task_id": str(task.id),
        "status": task.status,
        "assigned_to": agent.key if agent else None,
        "note": None if agent else f"No active '{p.role}' agent. Use propose_agent to create one.",
    }


class ReviewInput(ToolInput):
    task_id: UUID
    verdict: Literal["approve", "revise", "reject"]
    score: int = Field(ge=1, le=10, description="Quality 1-10 against the acceptance criteria.")
    feedback: str = Field(min_length=5, max_length=4000, description="Specific, actionable feedback.")


@tool(
    "review_task",
    description="Review a specialist's submitted result: approve, request a revision, or reject.",
    input_model=ReviewInput,
    tier=TIER_AUTOMATIC,
    scopes={"tasks:review"},
)
def review_task(ctx: ToolContext, p: ReviewInput) -> dict:
    task = ctx.session.get(Task, p.task_id)
    if task is None:
        raise ToolError("Task not found.")
    try:
        status = lifecycle.review(ctx.session, task, reviewer=ctx.agent.key, verdict=p.verdict, score=p.score,
                                  feedback=p.feedback)
    except lifecycle.LifecycleError as exc:
        raise ToolError(str(exc)) from exc
    return {"task_id": str(task.id), "new_status": status}


class CancelInput(ToolInput):
    task_id: UUID
    reason: str = Field(min_length=5, max_length=1000)


@tool(
    "cancel_task",
    description="Cancel an open task (and its queued sub-tasks) that is no longer needed.",
    input_model=CancelInput,
    tier=TIER_AUTOMATIC,
    scopes={"tasks:create"},
)
def cancel_task(ctx: ToolContext, p: CancelInput) -> dict:
    task = ctx.session.get(Task, p.task_id)
    if task is None:
        raise ToolError("Task not found.")
    if task.kind != "work":
        raise ToolError("Only work tasks can be cancelled.")
    try:
        lifecycle.cancel_task(ctx.session, task, ctx.agent.key, p.reason)
    except lifecycle.LifecycleError as exc:
        raise ToolError(str(exc)) from exc
    return {"task_id": str(task.id), "status": "cancelled"}


# --------------------------------------------------------------------------- owner chat


class ReplyInput(ToolInput):
    message: str = Field(min_length=1, max_length=8000, description="What you say to the owner (markdown).")


@tool(
    "reply_to_owner",
    description="Send a message to the owner in the dashboard chat. Speak in the first person, plainly and honestly.",
    input_model=ReplyInput,
    tier=TIER_AUTOMATIC,
    scopes={"chat:write"},
    daily_limit=200,
)
def reply_to_owner(ctx: ToolContext, p: ReplyInput) -> dict:
    msg = ChatMessage(role="manager", author=ctx.agent.name, content=p.message,
                      task_id=ctx.task.id if ctx.task else None)
    ctx.session.add(msg)
    ctx.session.flush()
    ctx.effects["replied"] = True
    return {"message_id": str(msg.id), "delivered": True}
