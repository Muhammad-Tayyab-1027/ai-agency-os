"""Agent registry: role templates, agent creation (with the agent cap), metrics and allocation.

Agents are data. A template (YAML in ./templates) fixes the *maximum* tools and scopes a role
may ever hold; the Manager can propose agents only from templates, never invent permissions.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.audit import audit
from app.core.runtime import get_setting
from app.db.enums import TASK_OPEN_STATUSES
from app.db.models import Agent, Approval, LlmUsage, Review, Task

TEMPLATE_DIR = Path(__file__).parent / "templates"
COUNTED_STATUSES = ("active", "paused")


class RegistryError(Exception):
    pass


@lru_cache
def templates() -> dict[str, dict[str, Any]]:
    out = {}
    for path in sorted(TEMPLATE_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        out[data["key"]] = data
    return out


def model_for_tier(tier: str) -> str:
    s = get_settings()
    return {"manager": s.manager_model, "specialist": s.specialist_model, "fast": s.fast_model}[tier]


def render_prompt(template: dict[str, Any]) -> str:
    s = get_settings()
    return template["system_prompt"].format(
        agency_name=s.agency_name,
        target_markets=", ".join(s.target_markets),
        target_niches="; ".join(s.target_niches),
    )


def agent_count(session: Session) -> int:
    return session.execute(
        select(func.count()).select_from(Agent).where(Agent.status.in_(COUNTED_STATUSES))
    ).scalar_one()


def pending_agent_proposals(session: Session) -> int:
    return session.execute(
        select(func.count())
        .select_from(Approval)
        .where(Approval.tool_name == "propose_agent", Approval.status.in_(("pending", "approved")))
    ).scalar_one()


def create_agent_from_template(
    session: Session,
    template_key: str,
    *,
    key: str | None = None,
    name: str | None = None,
    extra_purpose: str | None = None,
    tools_subset: list[str] | None = None,
    created_by: str = "owner",
) -> Agent:
    from app.tools.base import load_tools

    tpl = templates().get(template_key)
    if tpl is None:
        raise RegistryError(f"Unknown template '{template_key}'.")
    key = key or template_key
    if session.execute(select(Agent).where(Agent.key == key)).scalar_one_or_none():
        raise RegistryError(f"An agent with key '{key}' already exists.")
    if tpl.get("singleton") and session.execute(
        select(Agent).where(Agent.template == template_key, Agent.status != "retired")
    ).first():
        raise RegistryError(f"Only one '{template_key}' agent may exist.")
    limit = int(get_setting(session, "max_agents"))
    if agent_count(session) >= limit:
        raise RegistryError(f"Agent limit reached ({limit}). Retire an agent or raise the limit.")

    registry = load_tools()
    allowed = list(tpl["tools"])
    if tools_subset:
        unknown = set(tools_subset) - set(allowed)
        if unknown:
            raise RegistryError(f"Tools {sorted(unknown)} are not permitted for template '{template_key}'.")
        allowed = [t for t in allowed if t in tools_subset]
    missing = [t for t in allowed if t not in registry]
    if missing:
        raise RegistryError(f"Template '{template_key}' references unknown tools {missing}.")

    purpose = tpl["purpose"].strip()
    if extra_purpose:
        purpose = f"{purpose}\nFocus: {extra_purpose.strip()}"
    agent = Agent(
        key=key,
        name=name or tpl["name"],
        template=template_key,
        purpose=purpose,
        system_prompt=render_prompt(tpl),
        model=model_for_tier(tpl.get("model_tier", "specialist")),
        effort=tpl.get("effort", "medium"),
        allowed_tools=allowed,
        scopes=list(tpl.get("scopes", [])),
        autonomy_overrides=dict(tpl.get("autonomy_overrides") or {}),
        success_criteria=list(tpl.get("success_criteria", [])),
        kpis=dict(tpl.get("kpis") or {}),
        daily_budget_usd=get_settings().default_agent_daily_budget_usd,
        status="active",
        created_by=created_by,
    )
    session.add(agent)
    session.flush()
    audit(
        session, actor_type="user" if created_by == "owner" else "agent", actor=created_by,
        action="agent.created", target_type="agent", target_id=agent.id,
        data={"key": key, "template": template_key, "tools": allowed},
    )
    return agent


def ensure_manager(session: Session) -> Agent:
    manager = session.execute(select(Agent).where(Agent.template == "manager")).scalar_one_or_none()
    if manager is None:
        manager = create_agent_from_template(session, "manager", created_by="owner")
        manager.daily_budget_usd = max(manager.daily_budget_usd, get_settings().daily_llm_budget_usd / 2)
    return manager


def get_manager(session: Session) -> Agent | None:
    return session.execute(
        select(Agent).where(Agent.template == "manager", Agent.status == "active")
    ).scalar_one_or_none()


# --------------------------------------------------------------------------- metrics


def agent_metrics(session: Session) -> dict[str, dict[str, Any]]:
    """Per-agent performance, used for the dashboard and for task allocation."""
    task_rows = session.execute(
        select(
            Task.assigned_agent_id,
            func.count().filter(Task.status == "completed"),
            func.count().filter(Task.status == "failed"),
            func.count().filter(Task.status.in_(TASK_OPEN_STATUSES)),
            func.coalesce(func.sum(Task.revision_count), 0),
        )
        .where(Task.assigned_agent_id.is_not(None), Task.kind == "work")
        .group_by(Task.assigned_agent_id)
    ).all()
    review_rows = dict(
        session.execute(
            select(Review.agent_id, func.avg(Review.score)).group_by(Review.agent_id)
        ).all()
    )
    cost_rows = dict(
        session.execute(select(LlmUsage.agent_id, func.sum(LlmUsage.cost_usd)).group_by(LlmUsage.agent_id)).all()
    )
    out: dict[str, dict[str, Any]] = {}
    for agent_id, done, failed, open_, revisions in task_rows:
        finished = done + failed
        out[str(agent_id)] = {
            "completed": done,
            "failed": failed,
            "open": open_,
            "revisions": int(revisions),
            "success_rate": round(done / finished, 3) if finished else None,
            "avg_review_score": None,
            "cost_usd": 0.0,
        }
    for agent_id, avg in review_rows.items():
        out.setdefault(str(agent_id), _empty_metrics())["avg_review_score"] = round(float(avg), 2)
    for agent_id, cost in cost_rows.items():
        if agent_id is not None:
            out.setdefault(str(agent_id), _empty_metrics())["cost_usd"] = round(float(cost or 0), 4)
    return out


def _empty_metrics() -> dict[str, Any]:
    return {"completed": 0, "failed": 0, "open": 0, "revisions": 0, "success_rate": None,
            "avg_review_score": None, "cost_usd": 0.0}


def allocation_score(m: dict[str, Any]) -> float:
    """Higher is better. New agents get a neutral prior so they receive work."""
    success = m["success_rate"] if m["success_rate"] is not None else 0.75
    quality = (m["avg_review_score"] / 10) if m["avg_review_score"] is not None else 0.75
    return 0.6 * success + 0.3 * quality - 0.05 * m["open"]


def best_agent_for_role(session: Session, role: str) -> Agent | None:
    candidates = session.execute(
        select(Agent).where(Agent.template == role, Agent.status == "active").order_by(
            case((Agent.created_by == "owner", 0), else_=1), Agent.created_at
        )
    ).scalars().all()
    if not candidates:
        return None
    metrics = agent_metrics(session)
    return max(candidates, key=lambda a: allocation_score(metrics.get(str(a.id), _empty_metrics())))
