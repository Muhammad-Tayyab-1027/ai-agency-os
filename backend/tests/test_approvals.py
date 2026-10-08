import pytest
from sqlalchemy import select

from app.core.runtime import set_setting
from app.db.models import Agent, Approval, Task
from app.db.session import scoped_session
from app.orchestrator import approvals, lifecycle
from app.policy.engine import invoke
from app.tools.base import ToolContext, load_tools


def propose(role="lead_research", key=None):
    load_tools()
    with scoped_session(owner=True) as s:
        agent = s.execute(select(Agent).where(Agent.key == "manager")).scalar_one()
        payload = {"role": role, "reason": "Needed to grow revenue now."}
        if key:
            payload["key"] = key
        d = invoke(ToolContext(session=s, agent=agent), "propose_agent", payload)
        return d


def test_approve_executes_snapshot_and_assigns_waiting_tasks(manager):
    with scoped_session(owner=True) as s:
        blocked = lifecycle.create_task(s, title="Find leads", description="Find some leads", required_role="lead_research",
                                        requested_by="manager")
        assert blocked.status == "blocked"
        blocked_id = blocked.id
    d = propose()
    approval = approvals.approve(d.result["approval_id"], decided_by="owner@example.com")
    assert approval.status == "executed", approval.result
    with scoped_session(owner=True) as s:
        agent = s.execute(select(Agent).where(Agent.key == "lead_research")).scalar_one()
        assert agent.created_by == "manager"
        assert "lead_upsert" in agent.allowed_tools
        task = s.get(Task, blocked_id)
        assert task.status == "queued" and task.assigned_agent_id == agent.id


def test_edited_payload_is_validated_and_used(manager):
    d = propose()
    approval = approvals.approve(d.result["approval_id"], decided_by="owner",
                                 edited_payload={"role": "lead_research", "key": "uk_leads",
                                                 "reason": "Owner wants a UK-focused agent."})
    assert approval.status == "executed"
    with scoped_session(owner=True) as s:
        assert s.execute(select(Agent).where(Agent.key == "uk_leads")).scalar_one()

    d2 = propose("sales")
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(d2.result["approval_id"], decided_by="owner", edited_payload={"role": "sales", "bogus": 1})


def test_reject_does_not_execute(manager):
    d = propose()
    approvals.reject(d.result["approval_id"], decided_by="owner", note="Not yet")
    with scoped_session(owner=True) as s:
        assert s.execute(select(Agent).where(Agent.key == "lead_research")).first() is None
        assert s.get(Approval, d.result["approval_id"]).status == "rejected"
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(d.result["approval_id"], decided_by="owner")


def test_tampered_payload_is_refused(manager):
    d = propose()
    with scoped_session(owner=True) as s:
        s.get(Approval, d.result["approval_id"]).payload = {"role": "sales", "reason": "tampered with here"}
    with pytest.raises(approvals.ApprovalError, match="Integrity"):
        approvals.approve(d.result["approval_id"], decided_by="owner")


def test_agent_cap_counts_pending_proposals(manager):
    with scoped_session(owner=True) as s:
        set_setting(s, "max_agents", 2)
    assert propose().outcome == "queued"
    second = propose("sales")
    assert second.outcome == "error" and "limit" in second.result["error"]


def test_duplicate_proposal_rejected(manager):
    assert propose().outcome == "queued"
    again = propose()
    assert again.outcome == "error" and "already waiting" in again.result["error"]
