from sqlalchemy import select

from app.core.runtime import set_setting
from app.db.models import Agent, Approval, AuditLog
from app.db.session import scoped_session
from app.policy.engine import invoke
from app.tools.base import ToolContext, load_tools


def ctx_for(session, key="manager"):
    load_tools()
    agent = session.execute(select(Agent).where(Agent.key == key)).scalar_one()
    return ToolContext(session=session, agent=agent)


def test_tier_a_tool_executes_and_is_audited(manager):
    with scoped_session(owner=True) as s:
        d = invoke(ctx_for(s), "kb_write", {"title": "Playbook", "content": "Always cite evidence in outreach."})
        assert d.outcome == "executed", d.result
        actions = s.execute(select(AuditLog.action).order_by(AuditLog.id.desc())).scalars().first()
        assert actions == "tool.executed"


def test_tool_not_in_allow_list_is_denied(manager):
    with scoped_session(owner=True) as s:
        d = invoke(ctx_for(s), "lead_upsert", {})
        assert d.outcome == "denied"
        assert "allow-list" in d.result["error"]


def test_missing_scope_is_denied(manager):
    with scoped_session(owner=True) as s:
        ctx = ctx_for(s)
        ctx.agent.scopes = [x for x in ctx.agent.scopes if x != "kb:write"]
        d = invoke(ctx, "kb_write", {"title": "x" * 5, "content": "y" * 20})
        assert d.outcome == "denied" and "scopes" in d.result["error"]


def test_invalid_input_is_rejected(manager):
    with scoped_session(owner=True) as s:
        d = invoke(ctx_for(s), "kb_write", {"title": "ok title", "content": "short", "evil": 1})
        assert d.outcome == "error" and d.result["error"] == "Invalid input"


def test_tier_b_tool_is_queued_not_executed(manager):
    with scoped_session(owner=True) as s:
        d = invoke(ctx_for(s), "propose_agent", {"role": "lead_research", "reason": "Need leads to start selling."})
        assert d.outcome == "queued"
        approval = s.execute(select(Approval)).scalar_one()
        assert approval.status == "pending" and approval.tool_name == "propose_agent"
        assert s.execute(select(Agent).where(Agent.key == "lead_research")).first() is None


def test_tier_c_override_is_never_executed(manager):
    with scoped_session(owner=True) as s:
        ctx = ctx_for(s)
        ctx.agent.autonomy_overrides = {"kb_write": "C"}
        d = invoke(ctx, "kb_write", {"title": "Playbook", "content": "Always cite evidence."})
        assert d.outcome == "denied" and "Level C" in d.result["error"]


def test_overrides_cannot_loosen_a_tier(manager):
    with scoped_session(owner=True) as s:
        ctx = ctx_for(s)
        ctx.agent.autonomy_overrides = {"propose_agent": "A"}
        d = invoke(ctx, "propose_agent", {"role": "sales", "reason": "Need a sales agent now."})
        assert d.outcome == "queued"


def test_kill_switch_blocks_everything(manager):
    with scoped_session(owner=True) as s:
        set_setting(s, "paused", True)
        d = invoke(ctx_for(s), "kb_write", {"title": "Playbook", "content": "Always cite evidence."})
        assert d.outcome == "denied" and "paused" in d.result["error"]


def test_daily_rate_limit(manager):
    from app.tools.base import REGISTRY

    load_tools()
    spec = REGISTRY["kb_write"]
    original = spec.daily_limit
    spec.daily_limit = 2
    try:
        with scoped_session(owner=True) as s:
            ctx = ctx_for(s)
            outcomes = [invoke(ctx, "kb_write", {"title": f"Note {i}", "content": "content long enough"}).outcome
                        for i in range(3)]
        assert outcomes == ["executed", "executed", "denied"]
    finally:
        spec.daily_limit = original
