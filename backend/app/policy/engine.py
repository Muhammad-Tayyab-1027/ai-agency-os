"""Policy engine: the single gate every agent tool call passes through.

Order of checks (fail closed at every step):
1. global kill switch
2. agent is active and the tool is in its allow-list
3. the agent holds every scope the tool requires
4. input validation against the tool's schema
5. autonomy tier (agent overrides may only make a tool *stricter*)
     A -> execute, B -> queue an approval (unless executing an approved snapshot),
     C -> refuse; the agent must ask the owner via request_human_action
6. per-agent, per-tool daily rate limit
7. execute inside a savepoint, audit the outcome
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.core.audit import audit, redact
from app.core.runtime import consume_rate, is_paused, record_error
from app.db.enums import TIER_APPROVAL, TIER_AUTOMATIC, TIER_NEVER, TIER_ORDER
from app.db.models import Approval
from app.tools.base import REGISTRY, ToolContext, ToolError, ToolSpec


@dataclass
class Decision:
    outcome: str  # executed | queued | denied | error
    result: dict[str, Any]

    @property
    def is_error(self) -> bool:
        return self.outcome in ("denied", "error")

    def as_tool_result(self) -> str:
        return json.dumps({"outcome": self.outcome, **self.result}, default=str)


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def effective_tier(spec: ToolSpec, overrides: dict[str, str]) -> str:
    override = overrides.get(spec.name)
    if override in TIER_ORDER and TIER_ORDER[override] > TIER_ORDER[spec.tier]:
        return override
    return spec.tier


def _deny(ctx: ToolContext, name: str, reason: str, data: dict | None = None) -> Decision:
    audit(
        ctx.session,
        actor_type="agent",
        actor=ctx.actor,
        action="tool.denied",
        target_type="tool",
        target_id=name,
        client_id=ctx.client_id,
        outcome="denied",
        data={"reason": reason, **(data or {})},
    )
    return Decision("denied", {"error": reason})


def invoke(ctx: ToolContext, name: str, raw_input: Any) -> Decision:
    session = ctx.session
    if is_paused(session):
        return _deny(ctx, name, "The agency is paused by the owner (kill switch). No actions may run.")

    spec = REGISTRY.get(name)
    agent = ctx.agent
    if spec is None:
        return _deny(ctx, name, f"Unknown tool '{name}'.")
    if agent.status != "active":
        return _deny(ctx, name, f"Agent '{agent.key}' is {agent.status}.")
    if name not in (agent.allowed_tools or []):
        return _deny(ctx, name, f"Tool '{name}' is not in this agent's allow-list.")
    missing = spec.scopes - set(agent.scopes or [])
    if missing:
        return _deny(ctx, name, f"Missing permission scopes: {sorted(missing)}.")

    try:
        params = spec.input_model.model_validate(raw_input if isinstance(raw_input, dict) else {})
    except ValidationError as exc:
        errors = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
        return Decision("error", {"error": "Invalid input", "details": errors})

    tier = effective_tier(spec, agent.autonomy_overrides or {})
    if tier == TIER_NEVER:
        return _deny(
            ctx,
            name,
            "This action is never automatic (Level C). Use request_human_action to give the "
            "owner exact steps instead.",
        )
    if tier == TIER_APPROVAL and not ctx.approved:
        if spec.precheck is not None:
            try:
                spec.precheck(ctx, params)
            except ToolError as exc:
                return Decision("error", {"error": str(exc)})
        return _queue_for_approval(ctx, spec, params)

    if spec.daily_limit is not None and not consume_rate(
        session, f"tool:{agent.key}:{name}", spec.daily_limit
    ):
        return _deny(ctx, name, f"Daily limit of {spec.daily_limit} calls reached for '{name}'.")

    try:
        with session.begin_nested():
            result = spec.handler(ctx, params)
    except ToolError as exc:
        audit(
            session, actor_type="agent", actor=ctx.actor, action="tool.failed", target_type="tool",
            target_id=name, client_id=ctx.client_id, outcome="error", data={"error": str(exc)},
        )
        return Decision("error", {"error": str(exc)})
    except Exception as exc:  # unexpected bug: record it, never hide it
        record_error(
            session, f"tool:{name}", f"{type(exc).__name__}: {exc}",
            task_id=ctx.task.id if ctx.task else None, agent_id=agent.id,
        )
        return Decision("error", {"error": f"Internal error in tool '{name}': {type(exc).__name__}"})

    audit(
        session,
        actor_type="agent",
        actor=ctx.actor,
        action="tool.executed",
        target_type="tool",
        target_id=name,
        client_id=ctx.client_id,
        data={
            "input": redact(params.model_dump(mode="json")),
            "approval_id": str(ctx.approval_id) if ctx.approval_id else None,
        },
    )
    return Decision("executed", result)


def _queue_for_approval(ctx: ToolContext, spec: ToolSpec, params) -> Decision:
    payload = params.model_dump(mode="json")
    title = spec.approval_title(params) if spec.approval_title else f"{ctx.agent.name}: {spec.name}"
    reason = str(payload.get("reason") or "")
    approval = Approval(
        kind="tool_call",
        tool_name=spec.name,
        agent_id=ctx.agent.id,
        task_id=ctx.task.id if ctx.task else None,
        goal_id=ctx.task.goal_id if ctx.task else None,
        client_id=ctx.client_id,
        title=title[:300],
        reason=reason,
        payload=payload,
        payload_hash=payload_hash(payload),
        expires_at=datetime.now(UTC) + timedelta(hours=get_settings().approval_ttl_hours),
    )
    ctx.session.add(approval)
    ctx.session.flush()
    audit(
        ctx.session,
        actor_type="agent",
        actor=ctx.actor,
        action="approval.requested",
        target_type="approval",
        target_id=approval.id,
        client_id=ctx.client_id,
        data={"tool": spec.name, "title": title},
    )
    return Decision(
        "queued",
        {
            "approval_id": str(approval.id),
            "message": "Queued for the owner's approval. It has NOT happened yet. Do not report it as "
            "done; you will be notified when the owner decides.",
        },
    )


__all__ = ["Decision", "invoke", "payload_hash", "effective_tier", "TIER_AUTOMATIC"]
