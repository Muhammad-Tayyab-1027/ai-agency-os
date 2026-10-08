"""End-to-end with the offline mock: goal -> plan -> approvals -> agents -> work -> review -> report."""

from decimal import Decimal

from sqlalchemy import select

from app.db.models import Agent, Approval, ChatMessage, Report, Review, Task, TaskRun
from app.db.session import scoped_session
from app.orchestrator import approvals, reports, services
from app.workers.worker import run_once, run_periodic


def drain(limit=50):
    n = 0
    while run_once("test") and n < limit:
        n += 1
    return n


def statuses():
    with scoped_session(owner=True) as s:
        return {t.title: t.status for t in s.execute(select(Task)).scalars()}


def test_full_cycle(manager):
    with scoped_session(owner=True) as s:
        goal, plan = services.create_goal(s, title="$5k/month from websites", target_revenue=Decimal("5000"),
                                          currency="usd", period="monthly", priority_services=["website"],
                                          notes=None, actor="owner@example.com")
        plan_id = plan.id
    drain()

    with scoped_session(owner=True) as s:
        assert s.get(Task, plan_id).status == "completed"
        pending = s.execute(select(Approval).where(Approval.status == "pending")).scalars().all()
        assert {a.payload["role"] for a in pending} == {"lead_research", "sales"}
        work = s.execute(select(Task).where(Task.kind == "work")).scalars().all()
        assert len(work) == 2 and all(t.status == "blocked" for t in work)  # waiting for agents
        assert s.execute(select(ChatMessage).where(ChatMessage.role == "manager")).first()
        approval_ids = [a.id for a in pending]

    for approval_id in approval_ids:
        assert approvals.approve(approval_id, decided_by="owner").status == "executed"

    drain()
    with scoped_session(owner=True) as s:
        work = s.execute(select(Task).where(Task.kind == "work")).scalars().all()
        assert all(t.status == "completed" for t in work), [(t.title, t.status, t.last_error) for t in work]
        # Sales depended on lead research, so it must have finished second.
        research = next(t for t in work if "Research" in t.title)
        sales = next(t for t in work if "outreach" in t.title)
        assert research.completed_at <= sales.completed_at
        # Mock output is honest about doing no real work.
        assert research.output["output"]["mock"] is True
        assert s.execute(select(Review)).scalars().all()
        assert s.execute(select(TaskRun).where(TaskRun.status == "succeeded")).scalars().all()

    # Approval decisions became events -> one batched check-in task for the Manager.
    run_periodic()
    drain()
    with scoped_session(owner=True) as s:
        checkins = s.execute(select(Task).where(Task.kind == "checkin")).scalars().all()
        assert len(checkins) == 1 and checkins[0].status == "completed"

    with scoped_session(owner=True) as s:
        reports.request_report(s, "daily")
    drain()
    with scoped_session(owner=True) as s:
        report = s.execute(select(Report)).scalar_one()
        assert report.content.startswith("Here's my report")
        assert "Figures (from the database)" in report.content
        posted = s.execute(select(ChatMessage).where(ChatMessage.kind == "report")).scalar_one()
        assert posted.report_id == report.id


def test_owner_chat_gets_answer(manager):
    with scoped_session(owner=True) as s:
        services.post_owner_message(s, text="How are we doing this week?", actor="owner@example.com")
    drain()
    with scoped_session(owner=True) as s:
        replies = s.execute(select(ChatMessage).where(ChatMessage.role == "manager")).scalars().all()
        assert len(replies) == 1
        assert "How are we doing" in replies[0].content


def test_agents_are_registered_from_templates_only(manager):
    with scoped_session(owner=True) as s:
        agents = s.execute(select(Agent)).scalars().all()
        assert [a.key for a in agents] == ["manager"]
        assert "propose_agent" in agents[0].allowed_tools
        assert "lead_upsert" not in agents[0].allowed_tools  # the Manager delegates, it doesn't research
