"""Registry tools: inspect agents and templates, propose new agents (needs owner approval)."""

import re

from pydantic import Field, field_validator
from sqlalchemy import func, select

from app.agents import registry
from app.db.enums import TIER_APPROVAL, TIER_AUTOMATIC
from app.db.models import Agent, Approval
from app.orchestrator.lifecycle import assign_waiting_tasks
from app.tools.base import ToolContext, ToolError, ToolInput, tool
from app.tools.orchestration import NoInput


@tool(
    "list_agents",
    description="List agents with their role, status, purpose and performance metrics.",
    input_model=NoInput,
    tier=TIER_AUTOMATIC,
    scopes={"agents:read"},
)
def list_agents(ctx: ToolContext, params: NoInput) -> dict:
    metrics = registry.agent_metrics(ctx.session)
    agents = ctx.session.execute(select(Agent).where(Agent.status != "retired")).scalars().all()
    return {
        "agent_limit": registry.get_setting(ctx.session, "max_agents"),
        "agents": [
            {"key": a.key, "role": a.template, "name": a.name, "status": a.status, "purpose": a.purpose,
             "metrics": metrics.get(str(a.id))}
            for a in agents
        ],
    }


@tool(
    "list_agent_templates",
    description="List the role templates new agents can be created from, with their tools and purpose.",
    input_model=NoInput,
    tier=TIER_AUTOMATIC,
    scopes={"agents:read"},
)
def list_agent_templates(ctx: ToolContext, params: NoInput) -> dict:
    return {
        "templates": [
            {"role": k, "name": t["name"], "purpose": t["purpose"].strip(), "tools": t["tools"],
             "planned_tools": t.get("planned_tools", []), "success_criteria": t.get("success_criteria", [])}
            for k, t in registry.templates().items()
            if not t.get("singleton")
        ]
    }


class ProposeAgentInput(ToolInput):
    role: str = Field(description="Template key, e.g. 'lead_research'.")
    key: str | None = Field(default=None, description="Unique agent key; defaults to the role.")
    name: str | None = Field(default=None, max_length=120)
    focus: str | None = Field(default=None, max_length=1000, description="Optional extra focus for this agent.")
    reason: str = Field(min_length=10, max_length=2000, description="Why the agency needs this agent now.")

    @field_validator("key")
    @classmethod
    def _key(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", v):
            raise ValueError("key must be lowercase letters, digits and underscores (3-64 chars)")
        return v


def _precheck(ctx: ToolContext, p: ProposeAgentInput) -> None:
    tpl = registry.templates().get(p.role)
    if tpl is None:
        raise ToolError(f"Unknown role '{p.role}'. Valid roles: {sorted(registry.templates())}.")
    if tpl.get("singleton"):
        raise ToolError(f"Role '{p.role}' cannot be proposed.")
    key = p.key or p.role
    if ctx.session.execute(select(Agent).where(Agent.key == key)).scalar_one_or_none():
        raise ToolError(f"An agent with key '{key}' already exists. Choose another key.")
    duplicate = ctx.session.execute(
        select(Approval.id).where(
            Approval.tool_name == "propose_agent",
            Approval.status == "pending",
            func.coalesce(Approval.payload["key"].astext, Approval.payload["role"].astext) == key,
        )
    ).first()
    if duplicate:
        raise ToolError(f"A proposal for '{key}' is already waiting for the owner's approval.")
    limit = int(registry.get_setting(ctx.session, "max_agents"))
    used = registry.agent_count(ctx.session) + registry.pending_agent_proposals(ctx.session)
    if used >= limit:
        raise ToolError(f"Agent limit reached ({used}/{limit} including pending proposals).")


def _title(p: ProposeAgentInput) -> str:
    return f"Create new agent: {p.name or p.role} ({p.key or p.role})"


@tool(
    "propose_agent",
    description=(
        "Propose creating a new specialist agent from a role template. Requires the owner's approval and "
        "counts toward the agent limit. Tasks waiting for this role are assigned once it exists."
    ),
    input_model=ProposeAgentInput,
    tier=TIER_APPROVAL,
    scopes={"agents:create"},
    approval_title=_title,
    precheck=_precheck,
)
def propose_agent(ctx: ToolContext, p: ProposeAgentInput) -> dict:
    # Runs only after the owner approves the snapshot (the policy engine queues it first).
    try:
        agent = registry.create_agent_from_template(
            ctx.session, p.role, key=p.key, name=p.name, extra_purpose=p.focus, created_by=ctx.agent.key
        )
    except registry.RegistryError as exc:
        raise ToolError(str(exc)) from exc
    assigned = assign_waiting_tasks(ctx.session, agent)
    return {"agent_key": agent.key, "agent_id": str(agent.id), "tasks_assigned": assigned}
