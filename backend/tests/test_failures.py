"""Failures are retried and then reported as failures - never faked as success."""

from sqlalchemy import select, text

from app.agents.registry import create_agent_from_template
from app.core.runtime import set_setting
from app.db.models import ErrorEvent, Task, TaskRun
from app.db.session import scoped_session
from app.llm.gateway import LLMError, LLMResponse, set_provider
from app.orchestrator import lifecycle
from app.workers.worker import run_once


class Failing:
    def complete(self, req):
        raise LLMError("simulated outage")


class Silent:
    """Never calls submit_result."""

    def complete(self, req):
        return LLMResponse(content=[{"type": "text", "text": "I think it's done!"}], stop_reason="end_turn",
                           model="mock")


class Expensive:
    def complete(self, req):
        return LLMResponse(content=[{"type": "text", "text": "thinking..."}], stop_reason="end_turn",
                           model="claude-opus-5-5", input_tokens=10_000_000, output_tokens=0)


def make_task():
    with scoped_session(owner=True) as s:
        agent = create_agent_from_template(s, "lead_research")
        task = lifecycle.create_task(s, title="Find leads", description="Find ten leads", agent=agent,
                                     requested_by="manager")
        return task.id


def force_due(task_id):
    with scoped_session(owner=True) as s:
        s.execute(text("UPDATE tasks SET next_run_at = now() WHERE id = :id"), {"id": task_id})


def test_llm_failures_retry_then_fail(manager):
    set_provider(Failing())
    task_id = make_task()
    for _ in range(3):
        force_due(task_id)
        assert run_once("t")
    with scoped_session(owner=True) as s:
        task = s.get(Task, task_id)
        assert task.status == "failed" and task.attempts == 3
        assert "simulated outage" in task.last_error
        assert len(s.execute(select(TaskRun).where(TaskRun.task_id == task_id)).scalars().all()) == 3
        assert s.execute(select(ErrorEvent).where(ErrorEvent.task_id == task_id)).first()


def test_agent_that_never_submits_is_not_marked_done(manager):
    set_provider(Silent())
    task_id = make_task()
    run_once("t")
    with scoped_session(owner=True) as s:
        task = s.get(Task, task_id)
        assert task.status == "queued" and task.attempts == 1
        assert "submit_result" in task.last_error


def test_budget_exhaustion_pauses_without_burning_attempts(manager):
    set_provider(Expensive())
    with scoped_session(owner=True) as s:
        set_setting(s, "daily_llm_budget_usd", 1.0)
    task_id = make_task()
    run_once("t")  # first call costs $40; the guard stops the run before the next LLM call
    force_due(task_id)
    run_once("t")  # refused by the budget guard before calling the LLM at all
    with scoped_session(owner=True) as s:
        task = s.get(Task, task_id)
        assert task.status == "queued"
        assert "budget" in task.last_error.lower()
        assert task.attempts == 0  # budget stops are not the agent's fault
        assert s.execute(select(ErrorEvent).where(ErrorEvent.source == "budget")).first()


def test_failed_dependency_blocks_dependents(manager):
    with scoped_session(owner=True) as s:
        agent = create_agent_from_template(s, "lead_research")
        first = lifecycle.create_task(s, title="A first", description="first task", agent=agent, requested_by="m")
        second = lifecycle.create_task(s, title="B second", description="second task", agent=agent,
                                       requested_by="m", depends_on=[first.id])
        lifecycle.fail_task(s, first, "boom")
        assert second.status == "blocked" and "Dependency failed" in second.last_error


def test_kill_switch_stops_claiming(manager):
    make_task()
    with scoped_session(owner=True) as s:
        set_setting(s, "paused", True)
    assert run_once("t") is False
