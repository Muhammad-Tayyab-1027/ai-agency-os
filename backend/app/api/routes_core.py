"""Auth, overview, goals, chat, reports, settings and logs."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import SESSION_COOKIE, CurrentUser, current_user, get_db, require
from app.api.serializers import chat_dict, goal_dict, report_dict
from app.config import get_settings
from app.core.audit import audit, verify_chain
from app.core.runtime import EDITABLE_SETTINGS, all_settings, consume_rate, set_setting
from app.core.security import create_session_token, verify_password
from app.db.models import AuditLog, ChatMessage, ErrorEvent, Goal, Report, Service, User
from app.orchestrator import reports, services
from app.tools.orchestration import agency_snapshot

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- auth


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    if not consume_rate(db, f"login:{ip}", 10, window=timedelta(minutes=15)):
        db.commit()
        raise HTTPException(429, "Too many sign-in attempts. Try again in 15 minutes.")
    user = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        audit(db, actor_type="user", actor=body.email.lower(), action="auth.login_failed", outcome="denied",
              data={"ip": ip})
        db.commit()
        raise HTTPException(401, "Wrong email or password.")
    audit(db, actor_type="user", actor=user.email, action="auth.login", data={"ip": ip})
    response.set_cookie(
        SESSION_COOKIE, create_session_token(user.id, user.role), httponly=True, samesite="strict",
        secure=get_settings().is_production, max_age=get_settings().session_ttl_hours * 3600, path="/",
    )
    return {"email": user.email, "role": user.role}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: CurrentUser = Depends(current_user)):
    return {"email": user.email, "role": user.role}


# --------------------------------------------------------------------------- overview


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    snap = agency_snapshot(db)
    snap["errors_unresolved"] = db.execute(
        select(func.count()).select_from(ErrorEvent).where(ErrorEvent.resolved.is_(False))
    ).scalar_one()
    snap["settings"] = all_settings(db)
    snap["llm_provider"] = get_settings().llm_provider
    return snap


# --------------------------------------------------------------------------- goals


class GoalIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    target_revenue: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    period: Literal["monthly", "quarterly", "yearly"] = "monthly"
    priority_services: list[str] = Field(default_factory=list, max_length=9)
    notes: str | None = Field(default=None, max_length=4000)


@router.get("/goals")
def list_goals(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    return [goal_dict(g) for g in db.execute(select(Goal).order_by(Goal.created_at.desc())).scalars()]


@router.post("/goals")
def create_goal(body: GoalIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require("operate"))):
    valid = set(db.execute(select(Service.key)).scalars())
    unknown = set(body.priority_services) - valid
    if unknown:
        raise HTTPException(422, f"Unknown services: {sorted(unknown)}")
    try:
        goal, task = services.create_goal(db, **body.model_dump(), actor=user.email)
    except services.ServiceError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"goal": goal_dict(goal), "plan_task_id": str(task.id)}


@router.post("/goals/{goal_id}/status")
def set_goal_status(goal_id: str, body: dict, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require("operate"))):
    goal = db.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found.")
    if body.get("status") not in ("active", "paused", "achieved", "abandoned"):
        raise HTTPException(422, "Invalid status.")
    goal.status = body["status"]
    audit(db, actor_type="user", actor=user.email, action="goal.status", target_type="goal", target_id=goal.id,
          data={"status": goal.status})
    return goal_dict(goal)


@router.get("/services")
def list_services(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    return [{"key": s.key, "name": s.name, "description": s.description, "base_price": float(s.base_price),
             "currency": s.currency, "active": s.active} for s in db.execute(select(Service)).scalars()]


# --------------------------------------------------------------------------- chat with the Manager


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


@router.get("/chat")
def chat_history(limit: int = 100, db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    rows = db.execute(select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(min(limit, 500))).scalars()
    return [chat_dict(m) for m in reversed(list(rows))]


@router.post("/chat")
def chat_send(body: ChatIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require("operate"))):
    try:
        msg, task = services.post_owner_message(db, text=body.message, actor=user.email)
    except services.ServiceError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"message": chat_dict(msg), "task_id": str(task.id)}


# --------------------------------------------------------------------------- reports


@router.get("/reports")
def list_reports(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    return [report_dict(r) for r in db.execute(select(Report).order_by(Report.created_at.desc()).limit(100)).scalars()]


@router.post("/reports")
def request_report(body: dict, db: Session = Depends(get_db), user: CurrentUser = Depends(require("operate"))):
    kind = body.get("kind", "daily")
    if kind not in ("daily", "weekly"):
        raise HTTPException(422, "kind must be daily or weekly")
    result = reports.request_report(db, kind)
    return {"requested": True, "id": str(result.id), "type": type(result).__name__}


# --------------------------------------------------------------------------- settings


class SettingsIn(BaseModel):
    values: dict[str, Any]


@router.get("/settings")
def get_settings_route(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    s = get_settings()
    return {
        "runtime": all_settings(db),
        "static": {
            "llm_provider": s.llm_provider, "manager_model": s.manager_model, "specialist_model": s.specialist_model,
            "anthropic_key_configured": s.anthropic_api_key is not None, "target_markets": s.target_markets,
            "target_niches": s.target_niches, "approval_ttl_hours": s.approval_ttl_hours,
        },
    }


@router.put("/settings")
def update_settings(body: SettingsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require("admin"))):
    validators = {
        "paused": lambda v: isinstance(v, bool),
        "max_agents": lambda v: isinstance(v, int) and 1 <= v <= 50,
        "daily_llm_budget_usd": lambda v: isinstance(v, (int, float)) and 0 <= v <= 10_000,
        "outreach_daily_cap": lambda v: isinstance(v, int) and 0 <= v <= 500,
        "max_open_tasks": lambda v: isinstance(v, int) and 1 <= v <= 1000,
    }
    for key, value in body.values.items():
        if key not in EDITABLE_SETTINGS or not validators[key](value):
            raise HTTPException(422, f"Invalid value for '{key}'.")
    for key, value in body.values.items():
        set_setting(db, key, value)
    audit(db, actor_type="user", actor=user.email, action="settings.updated", data=body.values)
    return all_settings(db)


# --------------------------------------------------------------------------- logs


@router.get("/audit")
def audit_log(limit: int = 200, action: str | None = None, db: Session = Depends(get_db),
              user: CurrentUser = Depends(require("read"))):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    return [{"id": r.id, "ts": r.ts, "actor_type": r.actor_type, "actor": r.actor, "action": r.action,
             "target_type": r.target_type, "target_id": r.target_id, "outcome": r.outcome, "data": r.data}
            for r in db.execute(q).scalars()]


@router.get("/audit/verify")
def audit_verify(db: Session = Depends(get_db), user: CurrentUser = Depends(require("read"))):
    return verify_chain(db)


@router.get("/errors")
def errors(include_resolved: bool = False, db: Session = Depends(get_db),
           user: CurrentUser = Depends(require("read"))):
    q = select(ErrorEvent).order_by(ErrorEvent.created_at.desc()).limit(300)
    if not include_resolved:
        q = q.where(ErrorEvent.resolved.is_(False))
    return [{"id": str(e.id), "source": e.source, "message": e.message, "detail": e.detail,
             "task_id": str(e.task_id) if e.task_id else None, "resolved": e.resolved, "created_at": e.created_at}
            for e in db.execute(q).scalars()]


@router.post("/errors/{error_id}/resolve")
def resolve_error(error_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require("operate"))):
    err = db.get(ErrorEvent, error_id)
    if err is None:
        raise HTTPException(404, "Not found.")
    err.resolved = True
    return {"ok": True}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "time": datetime.now(UTC)}
