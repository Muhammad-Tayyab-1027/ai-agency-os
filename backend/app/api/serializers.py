"""ORM -> JSON helpers for the API."""

from typing import Any

from app.db.models import Agent, Approval, ChatMessage, Goal, HumanTask, Lead, Report, Task


def _id(v) -> str | None:
    return str(v) if v is not None else None


def goal_dict(g: Goal) -> dict[str, Any]:
    return {"id": str(g.id), "title": g.title, "target_revenue": float(g.target_revenue), "currency": g.currency,
            "period": g.period, "priority_services": g.priority_services, "notes": g.notes, "strategy": g.strategy,
            "status": g.status, "created_at": g.created_at}


def agent_dict(a: Agent, metrics: dict | None = None) -> dict[str, Any]:
    return {"id": str(a.id), "key": a.key, "name": a.name, "role": a.template, "purpose": a.purpose,
            "model": a.model, "effort": a.effort, "tools": a.allowed_tools, "scopes": a.scopes,
            "success_criteria": a.success_criteria, "kpis": a.kpis, "daily_budget_usd": a.daily_budget_usd,
            "status": a.status, "created_by": a.created_by, "created_at": a.created_at, "metrics": metrics}


def task_dict(t: Task) -> dict[str, Any]:
    return {"id": str(t.id), "kind": t.kind, "title": t.title, "description": t.description, "status": t.status,
            "priority": t.priority, "agent": t.assigned_agent.key if t.assigned_agent else None,
            "required_role": t.required_role, "requested_by": t.requested_by, "goal_id": _id(t.goal_id),
            "parent_id": _id(t.parent_id), "client_id": _id(t.client_id), "inputs": t.inputs,
            "acceptance_criteria": t.acceptance_criteria, "output": t.output, "feedback": t.feedback,
            "attempts": t.attempts, "max_attempts": t.max_attempts, "revision_count": t.revision_count,
            "last_error": t.last_error, "deadline": t.deadline, "created_at": t.created_at,
            "updated_at": t.updated_at, "completed_at": t.completed_at}


def approval_dict(a: Approval) -> dict[str, Any]:
    return {"id": str(a.id), "kind": a.kind, "status": a.status, "tool": a.tool_name,
            "agent": a.agent.key if a.agent else None, "title": a.title, "reason": a.reason, "payload": a.payload,
            "edited_payload": a.edited_payload, "task_id": _id(a.task_id), "client_id": _id(a.client_id),
            "decided_by": a.decided_by, "decided_at": a.decided_at, "decision_note": a.decision_note,
            "result": a.result, "expires_at": a.expires_at, "created_at": a.created_at}


def human_task_dict(h: HumanTask) -> dict[str, Any]:
    return {"id": str(h.id), "title": h.title, "reason": h.reason, "steps": h.steps, "status": h.status,
            "created_by": h.created_by, "blocks_task_id": _id(h.blocks_task_id), "response_note": h.response_note,
            "created_at": h.created_at, "completed_at": h.completed_at}


def lead_dict(lead: Lead) -> dict[str, Any]:
    return {"id": str(lead.id), "business_name": lead.business_name, "website": lead.website, "email": lead.email,
            "phone": lead.phone, "contact_name": lead.contact_name, "city": lead.city, "country": lead.country,
            "category": lead.category, "source": lead.source, "source_url": lead.source_url, "stage": lead.stage,
            "score": lead.score, "score_reasons": lead.score_reasons, "evidence": lead.evidence,
            "recommended_service": lead.recommended_service, "notes": lead.notes, "opted_out": lead.opted_out,
            "created_by": lead.created_by, "created_at": lead.created_at, "updated_at": lead.updated_at}


def chat_dict(m: ChatMessage) -> dict[str, Any]:
    return {"id": str(m.id), "role": m.role, "author": m.author, "content": m.content, "kind": m.kind,
            "report_id": _id(m.report_id), "task_id": _id(m.task_id), "created_at": m.created_at}


def report_dict(r: Report) -> dict[str, Any]:
    return {"id": str(r.id), "kind": r.kind, "period_start": r.period_start, "period_end": r.period_end,
            "content": r.content, "data": r.data, "created_at": r.created_at}
