"""Approval queue: the owner approves, edits or rejects Level-B actions.

On approval the system executes *exactly* the stored snapshot (verified against its hash) or
the owner's edited version (re-validated against the tool's schema). The requesting agent's
permissions are re-checked at execution time.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from app.core.audit import audit
from app.db.models import Agent, Approval, Task
from app.db.session import scoped_session
from app.orchestrator.lifecycle import notify_manager
from app.policy.engine import invoke, payload_hash
from app.tools.base import REGISTRY, ToolContext, load_tools


class ApprovalError(Exception):
    pass


def _load_pending(session, approval_id: UUID) -> Approval:
    approval = session.execute(
        select(Approval).where(Approval.id == approval_id).with_for_update(of=Approval)
    ).scalar_one_or_none()
    if approval is None:
        raise ApprovalError("Approval not found.")
    if approval.status != "pending":
        raise ApprovalError(f"Approval is already {approval.status}.")
    if approval.expires_at <= datetime.now(UTC):
        raise ApprovalError("Approval has expired.")  # the scheduler marks it expired
    return approval


def approve(approval_id: UUID, *, decided_by: str, edited_payload: dict[str, Any] | None = None,
            note: str | None = None) -> Approval:
    load_tools()
    # 1) Record the decision (owner scope).
    with scoped_session(owner=True) as s:
        approval = _load_pending(s, approval_id)
        if payload_hash(approval.payload) != approval.payload_hash:
            approval.status = "failed"
            approval.result = {"error": "Stored payload does not match its hash; refusing to execute."}
            audit(s, actor_type="system", actor="approvals", action="approval.tamper_detected",
                  target_type="approval", target_id=approval.id, outcome="error")
            raise ApprovalError("Integrity check failed for this approval.")
        payload = approval.payload
        if edited_payload is not None:
            spec = REGISTRY.get(approval.tool_name)
            try:
                payload = spec.input_model.model_validate(edited_payload).model_dump(mode="json")
            except ValidationError as exc:
                raise ApprovalError(f"Edited payload is invalid: {exc.errors()[:3]}") from exc
            approval.edited_payload = payload
        approval.status = "approved"
        approval.decided_by = decided_by
        approval.decided_at = datetime.now(UTC)
        approval.decision_note = note
        audit(s, actor_type="user", actor=decided_by, action="approval.approved", target_type="approval",
              target_id=approval.id, client_id=approval.client_id, data={"edited": edited_payload is not None})
        client_id, agent_id, task_id, tool_name = approval.client_id, approval.agent_id, approval.task_id, approval.tool_name

    # 2) Execute inside the data scope of the original request, as the requesting agent.
    with scoped_session(client_id=client_id, owner=client_id is None and _is_full_access(agent_id)) as s:
        agent = s.get(Agent, agent_id)
        task = s.get(Task, task_id) if task_id else None
        ctx = ToolContext(session=s, agent=agent, task=task, approved=True, approval_id=approval_id)
        decision = invoke(ctx, tool_name, payload)

    # 3) Record the outcome and tell the Manager.
    with scoped_session(owner=True) as s:
        approval = s.get(Approval, approval_id)
        approval.status = "executed" if decision.outcome == "executed" else "failed"
        approval.result = {"outcome": decision.outcome, **decision.result}
        about = s.get(Task, task_id) if task_id else None
        notify_manager(
            s,
            f"Owner APPROVED '{approval.title}'. Execution {approval.status}: "
            f"{decision.result if decision.outcome != 'executed' else 'done'}. Note: {note or '-'}",
            about_task=about, data={"approval_id": str(approval.id)},
        )
        return approval


def _is_full_access(agent_id: UUID) -> bool:
    with scoped_session(owner=True) as s:
        agent = s.get(Agent, agent_id)
        return bool(agent and "clients:all" in (agent.scopes or []))


def reject(approval_id: UUID, *, decided_by: str, note: str) -> Approval:
    with scoped_session(owner=True) as s:
        approval = _load_pending(s, approval_id)
        approval.status = "rejected"
        approval.decided_by = decided_by
        approval.decided_at = datetime.now(UTC)
        approval.decision_note = note
        audit(s, actor_type="user", actor=decided_by, action="approval.rejected", target_type="approval",
              target_id=approval.id, client_id=approval.client_id, data={"note": note})
        about = s.get(Task, approval.task_id) if approval.task_id else None
        notify_manager(s, f"Owner REJECTED '{approval.title}'. Reason: {note}", about_task=about,
                       data={"approval_id": str(approval.id)})
        return approval


def expire_stale(session) -> int:
    rows = session.execute(
        select(Approval).where(Approval.status == "pending", Approval.expires_at <= datetime.now(UTC))
    ).scalars().all()
    for approval in rows:
        approval.status = "expired"
        notify_manager(session, f"Approval request expired without a decision: '{approval.title}'.",
                       data={"approval_id": str(approval.id)})
    return len(rows)
