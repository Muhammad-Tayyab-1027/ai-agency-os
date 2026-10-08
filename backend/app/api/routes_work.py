"""Agents, tasks, approvals, human tasks, pipeline, clients, revenue and knowledge."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.registry import agent_metrics, templates
from app.api.deps import CurrentUser, get_db, require
from app.api.serializers import agent_dict, approval_dict, human_task_dict, lead_dict, task_dict
from app.core.audit import audit
from app.db.enums import PIPELINE_STAGES
from app.db.models import (
    Agent,
    AgentMessage,
    Approval,
    Client,
    HumanTask,
    KnowledgeDoc,
    Lead,
    LeadEvent,
    Project,
    Review,
    RevenueEvent,
    SuppressionEntry,
    Task,
    TaskRun,
)
from app.orchestrator import approvals, lifecycle

router = APIRouter(prefix="/api")


def _get(db: Session, model, obj_id):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} not found.")
    return obj


# --------------------------------------------------------------------------- agents


@router.get("/agents")
def list_agents(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    metrics = agent_metrics(db)
    return [agent_dict(a, metrics.get(str(a.id))) for a in db.execute(select(Agent).order_by(Agent.created_at)).scalars()]


@router.get("/agent-templates")
def list_templates(user: CurrentUser = Depends(require("read"))):
    return [{"role": k, "name": t["name"], "purpose": t["purpose"].strip(), "tools": t["tools"],
             "planned_tools": t.get("planned_tools", []), "success_criteria": t.get("success_criteria", [])}
            for k, t in templates().items()]


class AgentStatusIn(BaseModel):
    status: Literal["active", "paused", "retired"]


@router.post("/agents/{agent_id}/status")
def set_agent_status(agent_id: UUID, body: AgentStatusIn, db: Session = Depends(get_db),
                     user: CurrentUser = Depends(require("operate"))):
    agent = _get(db, Agent, agent_id)
    if agent.template == "manager" and body.status == "retired":
        raise HTTPException(409, "The Manager cannot be retired (pause it instead).")
    agent.status = body.status
    audit(db, actor_type="user", actor=user.email, action="agent.status", target_type="agent", target_id=agent.id,
          data={"status": body.status})
    return agent_dict(agent)


# --------------------------------------------------------------------------- tasks


@router.get("/tasks")
def list_tasks(status: str | None = None, kind: str | None = None, agent: str | None = None, limit: int = 200,
               db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    q = select(Task).order_by(Task.created_at.desc()).limit(min(limit, 1000))
    if status:
        q = q.where(Task.status == status)
    if kind:
        q = q.where(Task.kind == kind)
    if agent:
        q = q.join(Agent, Agent.id == Task.assigned_agent_id).where(Agent.key == agent)
    return [task_dict(t) for t in db.execute(q).unique().scalars()]


@router.get("/tasks/{task_id}")
def get_task(task_id: UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    task = _get(db, Task, task_id)
    runs = db.execute(select(TaskRun).where(TaskRun.task_id == task_id).order_by(TaskRun.started_at)).scalars()
    reviews = db.execute(select(Review).where(Review.task_id == task_id).order_by(Review.created_at)).scalars()
    messages = db.execute(select(AgentMessage).where(AgentMessage.task_id == task_id)
                          .order_by(AgentMessage.created_at)).scalars()
    children = db.execute(select(Task).where(Task.parent_id == task_id)).unique().scalars()
    return {
        "task": task_dict(task),
        "runs": [{"id": str(r.id), "attempt": r.attempt, "status": r.status, "tool_calls": r.tool_calls,
                  "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "cost_usd": r.cost_usd,
                  "stop_reason": r.stop_reason, "error": r.error, "started_at": r.started_at,
                  "finished_at": r.finished_at, "transcript": r.transcript} for r in runs],
        "reviews": [{"verdict": r.verdict, "score": r.score, "feedback": r.feedback, "reviewer": r.reviewer,
                     "created_at": r.created_at} for r in reviews],
        "messages": [{"type": m.type, "from": m.from_agent, "to": m.to_agent, "payload": m.payload,
                      "created_at": m.created_at} for m in messages],
        "children": [task_dict(c) for c in children],
    }


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(require("operate"))):
    task = _get(db, Task, task_id)
    if task.status not in ("failed", "blocked", "cancelled"):
        raise HTTPException(409, f"Task is {task.status}; only failed, blocked or cancelled tasks can be retried.")
    if task.assigned_agent_id is None:
        raise HTTPException(409, f"No agent with role '{task.required_role}' exists yet.")
    task.status, task.attempts, task.last_error = "queued", 0, None
    task.next_run_at = datetime.now(UTC)
    audit(db, actor_type="user", actor=user.email, action="task.retry", target_type="task", target_id=task.id)
    return task_dict(task)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: UUID, body: CancelIn, db: Session = Depends(get_db),
                user: CurrentUser = Depends(require("operate"))):
    task = _get(db, Task, task_id)
    try:
        lifecycle.cancel_task(db, task, "owner", body.reason)
    except lifecycle.LifecycleError as exc:
        raise HTTPException(409, str(exc)) from exc
    return task_dict(task)


# --------------------------------------------------------------------------- approvals


@router.get("/approvals")
def list_approvals(status: str | None = "pending", db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require("read"))):
    q = select(Approval).order_by(Approval.created_at.desc()).limit(300)
    if status:
        q = q.where(Approval.status == status)
    return [approval_dict(a) for a in db.execute(q).unique().scalars()]


class ApproveIn(BaseModel):
    edited_payload: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=2000)


class RejectIn(BaseModel):
    note: str = Field(min_length=3, max_length=2000)


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: UUID, body: ApproveIn, user: CurrentUser = Depends(require("approve"))):
    try:
        approval = approvals.approve(approval_id, decided_by=user.email, edited_payload=body.edited_payload,
                                     note=body.note)
    except approvals.ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": str(approval.id), "status": approval.status, "result": approval.result}


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: UUID, body: RejectIn, user: CurrentUser = Depends(require("approve"))):
    try:
        approval = approvals.reject(approval_id, decided_by=user.email, note=body.note)
    except approvals.ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": str(approval.id), "status": approval.status}


# --------------------------------------------------------------------------- human tasks


@router.get("/human-tasks")
def list_human_tasks(status: str | None = "open", db: Session = Depends(get_db),
                     user: CurrentUser = Depends(require("read"))):
    q = select(HumanTask).order_by(HumanTask.created_at.desc()).limit(300)
    if status:
        q = q.where(HumanTask.status == status)
    return [human_task_dict(h) for h in db.execute(q).scalars()]


class HumanDoneIn(BaseModel):
    note: str = Field(default="", max_length=4000, description="Never paste passwords or API keys here.")
    outcome: Literal["done", "cancelled"] = "done"


@router.post("/human-tasks/{human_id}/complete")
def complete_human_task(human_id: UUID, body: HumanDoneIn, db: Session = Depends(get_db),
                        user: CurrentUser = Depends(require("operate"))):
    human = _get(db, HumanTask, human_id)
    if human.status != "open":
        raise HTTPException(409, f"Already {human.status}.")
    human.status = body.outcome
    human.response_note = body.note
    human.completed_at = datetime.now(UTC)
    audit(db, actor_type="user", actor=user.email, action=f"human_task.{body.outcome}", target_type="human_task",
          target_id=human.id)
    if human.blocks_task_id:
        task = db.get(Task, human.blocks_task_id)
        if task and body.outcome == "done":
            lifecycle.resume_blocked_task(db, task, body.note)
    lifecycle.notify_manager(db, f"Owner marked '{human.title}' as {body.outcome}. Note: {body.note or '-'}")
    return human_task_dict(human)


# --------------------------------------------------------------------------- pipeline


@router.get("/leads")
def list_leads(stage: str | None = None, min_score: int | None = None, q: str | None = None, limit: int = 300,
               db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    query = select(Lead).order_by(Lead.score.desc().nulls_last(), Lead.created_at.desc()).limit(min(limit, 1000))
    if stage:
        query = query.where(Lead.stage == stage)
    if min_score is not None:
        query = query.where(Lead.score >= min_score)
    if q:
        query = query.where(Lead.business_name.ilike(f"%{q}%"))
    return [lead_dict(lead) for lead in db.execute(query).scalars()]


@router.get("/pipeline")
def pipeline(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    counts = dict(db.execute(select(Lead.stage, func.count()).group_by(Lead.stage)).all())
    return [{"stage": s, "count": counts.get(s, 0)} for s in PIPELINE_STAGES]


class StageIn(BaseModel):
    stage: Literal[PIPELINE_STAGES]  # type: ignore[valid-type]
    note: str = Field(default="", max_length=2000)


@router.post("/leads/{lead_id}/stage")
def move_lead(lead_id: UUID, body: StageIn, db: Session = Depends(get_db),
              user: CurrentUser = Depends(require("operate"))):
    lead = _get(db, Lead, lead_id)
    previous = lead.stage
    lead.stage = body.stage
    db.add(LeadEvent(lead_id=lead.id, from_stage=previous, to_stage=body.stage, actor=user.email, note=body.note))
    audit(db, actor_type="user", actor=user.email, action="lead.stage", target_type="lead", target_id=lead.id,
          data={"from": previous, "to": body.stage})
    return lead_dict(lead)


class OptOutIn(BaseModel):
    value: str = Field(min_length=3, max_length=320, description="email address or @domain")
    reason: str = Field(default="opt-out", max_length=200)


@router.get("/suppression")
def list_suppression(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    return [{"value": e.value, "reason": e.reason, "created_at": e.created_at}
            for e in db.execute(select(SuppressionEntry).order_by(SuppressionEntry.created_at.desc())).scalars()]


@router.post("/suppression")
def add_suppression(body: OptOutIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require("operate"))):
    value = body.value.strip().lower()
    if "@" not in value:
        raise HTTPException(422, "Use an email address or @domain.")
    if db.get(SuppressionEntry, value) is None:
        db.add(SuppressionEntry(value=value, reason=body.reason))
    # Mark matching leads opted out immediately.
    if value.startswith("@"):
        matches = db.execute(select(Lead).where(Lead.email.ilike(f"%{value}") | Lead.website.ilike(f"%{value[1:]}%")))
    else:
        matches = db.execute(select(Lead).where(func.lower(Lead.email) == value))
    for lead in matches.scalars():
        lead.opted_out = True
    audit(db, actor_type="user", actor=user.email, action="suppression.added", data={"value": value})
    return {"ok": True}


# --------------------------------------------------------------------------- clients, projects, revenue


class ClientIn(BaseModel):
    name: str = Field(min_length=2, max_length=300)
    email: str | None = Field(default=None, max_length=320)
    lead_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=4000)


@router.get("/clients")
def list_clients(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    rows = db.execute(select(Client).order_by(Client.created_at.desc())).scalars()
    return [{"id": str(c.id), "name": c.name, "email": c.email, "status": c.status, "notes": c.notes,
             "created_at": c.created_at} for c in rows]


@router.post("/clients")
def create_client(body: ClientIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require("operate"))):
    client = Client(**body.model_dump())
    db.add(client)
    db.flush()
    if body.lead_id and (lead := db.get(Lead, body.lead_id)):
        previous = lead.stage
        lead.stage = "won"
        db.add(LeadEvent(lead_id=lead.id, from_stage=previous, to_stage="won", actor=user.email, note="became client"))
    audit(db, actor_type="user", actor=user.email, action="client.created", target_type="client",
          target_id=client.id, client_id=client.id)
    return {"id": str(client.id), "name": client.name}


class ProjectIn(BaseModel):
    client_id: UUID
    name: str = Field(min_length=2, max_length=300)
    service_key: str | None = None
    value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    deadline: datetime | None = None


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    rows = db.execute(select(Project).order_by(Project.created_at.desc())).scalars()
    return [{"id": str(p.id), "client_id": str(p.client_id), "name": p.name, "service_key": p.service_key,
             "status": p.status, "value": float(p.value) if p.value is not None else None, "currency": p.currency,
             "deadline": p.deadline, "created_at": p.created_at} for p in rows]


@router.post("/projects")
def create_project(body: ProjectIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require("operate"))):
    _get(db, Client, body.client_id)
    project = Project(**body.model_dump())
    db.add(project)
    db.flush()
    audit(db, actor_type="user", actor=user.email, action="project.created", target_type="project",
          target_id=project.id, client_id=project.client_id)
    return {"id": str(project.id)}


class RevenueIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    client_id: UUID | None = None
    project_id: UUID | None = None
    service_key: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime | None = None


@router.get("/revenue")
def list_revenue(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    rows = db.execute(select(RevenueEvent).order_by(RevenueEvent.occurred_at.desc()).limit(500)).scalars()
    by_service = dict(db.execute(select(RevenueEvent.service_key, func.sum(RevenueEvent.amount))
                                 .group_by(RevenueEvent.service_key)).all())
    return {
        "events": [{"id": str(r.id), "amount": float(r.amount), "currency": r.currency,
                    "client_id": str(r.client_id) if r.client_id else None, "service_key": r.service_key,
                    "source": r.source, "note": r.note, "occurred_at": r.occurred_at} for r in rows],
        "by_service": {k or "unassigned": float(v) for k, v in by_service.items()},
    }


@router.post("/revenue")
def record_revenue(body: RevenueIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require("operate"))):
    data = body.model_dump(exclude_none=True)
    event = RevenueEvent(**data, source="manual")
    db.add(event)
    db.flush()
    audit(db, actor_type="user", actor=user.email, action="revenue.recorded", target_type="revenue",
          target_id=event.id, client_id=event.client_id, data={"amount": str(body.amount), "currency": body.currency})
    lifecycle.notify_manager(db, f"Owner recorded revenue: {body.amount} {body.currency} "
                                 f"({body.service_key or 'no service'}).")
    return {"id": str(event.id)}


# --------------------------------------------------------------------------- knowledge


@router.get("/knowledge")
def list_knowledge(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    rows = db.execute(select(KnowledgeDoc).order_by(KnowledgeDoc.created_at.desc()).limit(300)).scalars()
    return [{"id": str(d.id), "title": d.title, "content": d.content, "tags": d.tags, "source": d.source,
             "client_id": str(d.client_id) if d.client_id else None, "created_by": d.created_by,
             "created_at": d.created_at} for d in rows]
