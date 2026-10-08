"""Agent runtime: runs one agent on one task through the LLM tool-use loop.

Every tool call goes through the policy engine. A run ends when the agent calls
``submit_result`` (validated), or fails honestly (LLM error, refusal, no result, exceptions),
in which case the task's retry policy applies. Nothing is ever marked done without a result.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.agents.registry import templates
from app.config import get_settings
from app.core.audit import audit
from app.core.runtime import get_setting, is_paused, record_error, spent_today
from app.db.models import Agent, ChatMessage, Goal, LlmUsage, Task, TaskRun
from app.db.session import make_session, scoped_session
from app.llm.gateway import LLMError, LLMRequest, get_provider
from app.orchestrator import lifecycle
from app.policy.engine import invoke
from app.tools.base import ToolContext, load_tools

log = logging.getLogger("agency.runner")


class SubmitResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["success", "partial", "failed", "needs_human"]
    summary: str = Field(min_length=3, max_length=8000)
    output: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list, max_length=50)
    confidence: float = Field(default=0.7, ge=0, le=1)


SUBMIT_TOOL = {
    "name": "submit_result",
    "description": (
        "Finish the task. Call exactly once, after all other work. status: success (criteria met), partial "
        "(some criteria met - explain), failed (could not do it - explain why), needs_human (waiting for the "
        "owner). Be honest: never report work that no tool result confirms."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["success", "partial", "failed", "needs_human"]},
            "summary": {"type": "string", "description": "What was done, in plain language."},
            "output": {"type": "object", "description": "Structured result (ids created, findings, drafts)."},
            "evidence": {"type": "array", "items": {"type": "string"}, "description": "URLs or record ids."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["status", "summary"],
        "additionalProperties": False,
    },
}


class RunAborted(Exception):
    """Stop without counting a failed attempt (paused, out of budget)."""

    def __init__(self, reason: str, retry_at: datetime | None = None):
        super().__init__(reason)
        self.retry_at = retry_at


# --------------------------------------------------------------------------- prompt building


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def build_task_message(session, task: Task, agent: Agent) -> tuple[str, dict[str, Any]]:
    """The first user message: the task envelope plus kind-specific context. Returns (text, meta)."""
    envelope: dict[str, Any] = {
        "task_id": str(task.id),
        "kind": task.kind,
        "title": task.title,
        "description": task.description,
        "inputs": task.inputs,
        "acceptance_criteria": task.acceptance_criteria,
        "priority": task.priority,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "client_id": str(task.client_id) if task.client_id else None,
        "goal_id": str(task.goal_id) if task.goal_id else None,
        "attempt": task.attempts,
    }
    if task.feedback:
        envelope["previous_feedback"] = task.feedback
    if task.last_error:
        envelope["last_error"] = task.last_error
    meta: dict[str, Any] = {"agent_template": agent.template, "task_kind": task.kind, "task": _jsonable(envelope)}
    context: dict[str, Any] = {}

    if task.goal_id:
        goal = session.get(Goal, task.goal_id)
        if goal:
            context["goal"] = {"id": str(goal.id), "title": goal.title, "target_revenue": float(goal.target_revenue),
                               "currency": goal.currency, "period": goal.period,
                               "priority_services": goal.priority_services, "notes": goal.notes,
                               "strategy": goal.strategy}

    if agent.template == "manager":
        context["roles_available"] = {k: t["purpose"].strip() for k, t in templates().items() if not t.get("singleton")}
        context["agent_limit"] = get_setting(session, "max_agents")

    if task.kind == "review":
        target = session.get(Task, UUID(task.inputs["task_id"]))
        if target is not None:
            context["task_under_review"] = {
                "id": str(target.id), "title": target.title, "description": target.description,
                "acceptance_criteria": target.acceptance_criteria, "output": target.output,
                "assigned_agent": target.assigned_agent.key if target.assigned_agent else None,
                "revision": target.revision_count, "max_revisions": target.max_revisions,
            }
    elif task.kind == "chat":
        history = session.execute(select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(20)).scalars().all()
        context["conversation"] = [{"from": m.role, "at": m.created_at.isoformat(), "text": m.content[:3000]}
                                   for m in reversed(history)]
        meta["owner_message"] = task.inputs.get("message", "")

    instructions = {
        "work": "Complete the task, then call submit_result.",
        "plan": "Plan the goal: check status, write the strategy, propose any missing agents and create the "
                "first concrete tasks. Then tell the owner what you did with reply_to_owner, and call submit_result.",
        "review": "Review the task_under_review against its acceptance criteria. Call review_task, then submit_result.",
        "chat": "The owner sent you a message (the last 'owner' entry in conversation). Use tools to check facts if "
                "needed, answer them with reply_to_owner in the first person, take any agreed actions, then call "
                "submit_result.",
        "checkin": "These are updates since your last check-in. Decide whether anything needs action (re-plan, "
                   "retry, ask the owner) and act. Then call submit_result.",
        "report": "Write the report narrative for the owner in the first person from the data provided: what "
                  "happened, what worked, what failed and why, what you plan next, and exactly what you need from "
                  "them. Use only the numbers given. Put it in submit_result.output.narrative.",
    }[task.kind]
    text = (
        f"{instructions}\n\n<task>\n{json.dumps(envelope, indent=2, default=str)}\n</task>\n"
        f"<context>\n{json.dumps(context, indent=2, default=str)}\n</context>"
    )
    return text, meta


# --------------------------------------------------------------------------- the loop


def _tool_definitions(agent: Agent) -> list[dict]:
    registry = load_tools()
    defs = [registry[name].anthropic_schema() for name in agent.allowed_tools if name in registry]
    return [*defs, SUBMIT_TOOL]


def _check_guards(session, agent: Agent) -> None:
    if is_paused(session):
        raise RunAborted("Agency paused by the owner.")
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    if spent_today(session) >= float(get_setting(session, "daily_llm_budget_usd")):
        raise RunAborted("Daily LLM budget reached for the agency.", retry_at=tomorrow)
    if spent_today(session, agent.id) >= agent.daily_budget_usd:
        raise RunAborted(f"Daily LLM budget reached for agent '{agent.key}'.", retry_at=tomorrow)


def run_task(task_id: UUID, worker_id: str = "inline") -> str:
    """Run a claimed task (status 'running'). Returns the task's resulting status."""
    settings = get_settings()
    load_tools()
    with scoped_session(owner=True) as s:
        task = s.get(Task, task_id)
        if task is None or task.assigned_agent_id is None:
            return "missing"
        agent = s.get(Agent, task.assigned_agent_id)
        client_id = task.client_id
        full_access = "clients:all" in (agent.scopes or [])

    # Agents work inside their task's data scope; only the Manager sees across clients.
    session = make_session(client_id=None if full_access else client_id, owner=full_access)
    run: TaskRun | None = None
    try:
        task = session.get(Task, task_id)
        agent = session.get(Agent, task.assigned_agent_id)
        run = TaskRun(task_id=task.id, agent_id=agent.id, attempt=task.attempts, status="running", transcript=[])
        session.add(run)
        session.commit()

        first_message, meta = build_task_message(session, task, agent)
        messages: list[dict[str, Any]] = [{"role": "user", "content": first_message}]
        tools = _tool_definitions(agent)
        provider = get_provider()
        ctx = ToolContext(session=session, agent=agent, task=task, run_id=run.id)
        submitted: SubmitResult | None = None
        nudges = 0

        for _turn in range(settings.agent_max_turns):
            _check_guards(session, agent)
            response = provider.complete(LLMRequest(
                model=agent.model, system=agent.system_prompt, messages=messages, tools=tools,
                effort=agent.effort, max_tokens=settings.llm_max_tokens, meta=meta,
            ))
            session.add(LlmUsage(agent_id=agent.id, task_run_id=run.id, model=response.model,
                                 input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                                 cache_read_tokens=response.cache_read_tokens, cost_usd=response.cost_usd))
            run.input_tokens += response.input_tokens
            run.output_tokens += response.output_tokens
            run.cost_usd += response.cost_usd
            messages.append({"role": "assistant", "content": response.content})
            run.stop_reason = response.stop_reason

            if response.stop_reason == "refusal":
                raise LLMError(f"The model declined this task (category: {response.refusal_category}).")
            if response.stop_reason == "max_tokens":
                raise LLMError("The model ran out of output tokens before finishing.")

            tool_uses = response.tool_uses()
            if not tool_uses:
                nudges += 1
                if nudges > 2:
                    raise LLMError("Agent stopped without calling submit_result.")
                messages.append({"role": "user", "content": "You have not finished. Continue the task and end by "
                                 "calling submit_result exactly once."})
                _save_transcript(session, run, messages)
                continue

            results = []
            submit_blocks = [b for b in tool_uses if b["name"] == "submit_result"]
            for block in tool_uses:
                if block["name"] == "submit_result":
                    continue
                decision = invoke(ctx, block["name"], block.get("input"))
                run.tool_calls += 1
                results.append({"type": "tool_result", "tool_use_id": block["id"],
                                "content": decision.as_tool_result(), "is_error": decision.is_error})
            for block in submit_blocks:
                if submitted is not None:
                    results.append({"type": "tool_result", "tool_use_id": block["id"], "is_error": True,
                                    "content": "submit_result was already accepted."})
                    continue
                try:
                    submitted = SubmitResult.model_validate(block.get("input") or {})
                    results.append({"type": "tool_result", "tool_use_id": block["id"], "content": "Result accepted."})
                except ValidationError as exc:
                    results.append({"type": "tool_result", "tool_use_id": block["id"], "is_error": True,
                                    "content": f"Invalid submit_result: {exc.errors()[:3]}"})
            messages.append({"role": "user", "content": results})
            _save_transcript(session, run, messages)
            if submitted is not None:
                break
        else:
            raise LLMError(f"Agent did not finish within {settings.agent_max_turns} turns.")

        if submitted is None:
            raise LLMError("Agent finished without a valid submit_result.")
        _finalize(session, task, agent, submitted, ctx)
        run.status = "succeeded"
        run.finished_at = datetime.now(UTC)
        task.locked_by = None
        _save_transcript(session, run, messages)
        session.commit()
        return task.status

    except RunAborted as exc:
        session.rollback()
        return _abort(task_id, run.id if run else None, str(exc), exc.retry_at)
    except Exception as exc:  # LLMError, AgentReportedFailure, LifecycleError, bugs
        session.rollback()
        if not isinstance(exc, (LLMError, lifecycle.AgentReportedFailure, lifecycle.LifecycleError)):
            log.exception("unexpected error running task %s", task_id)
        return _fail_attempt(task_id, run.id if run else None, f"{type(exc).__name__}: {exc}")
    finally:
        session.close()


def _save_transcript(session, run: TaskRun, messages: list[dict]) -> None:
    run.transcript = _jsonable(messages)
    session.commit()


def _finalize(session, task: Task, agent: Agent, result: SubmitResult, ctx: ToolContext) -> None:
    data = result.model_dump()
    if task.kind == "review":
        target = session.get(Task, UUID(task.inputs["task_id"]))
        if target is not None and target.status == "in_review":
            raise lifecycle.LifecycleError("Review finished without calling review_task.")
    elif task.kind == "chat" and not ctx.effects.get("replied"):
        # The owner always gets an answer, even if the Manager forgot to reply explicitly.
        session.add(ChatMessage(role="manager", author=agent.name, content=result.summary, task_id=task.id))
    elif task.kind == "report":
        from app.orchestrator.reports import finalize_report

        finalize_report(session, task, agent, result.output.get("narrative") or result.summary)

    if task.kind == "work":
        lifecycle.submit_result(session, task, agent, data)
    else:
        if result.status == "failed":
            raise lifecycle.AgentReportedFailure(result.summary)
        task.output = data
        lifecycle.complete_task(session, task)
    audit(session, actor_type="agent", actor=agent.key, action="task.submitted", target_type="task",
          target_id=task.id, client_id=task.client_id, data={"status": result.status, "summary": result.summary[:500]})


def _abort(task_id: UUID, run_id: UUID | None, reason: str, retry_at: datetime | None) -> str:
    with scoped_session(owner=True) as s:
        task = s.get(Task, task_id)
        task.status = "queued"
        task.attempts = max(task.attempts - 1, 0)  # not the agent's fault
        task.locked_by = None
        task.next_run_at = retry_at or datetime.now(UTC) + timedelta(minutes=5)
        task.last_error = reason
        if run_id:
            run = s.get(TaskRun, run_id)
            run.status, run.error, run.finished_at = "aborted", reason, datetime.now(UTC)
        if retry_at:
            record_error(s, "budget", reason, task_id=task_id, agent_id=task.assigned_agent_id)
        return task.status


def _fail_attempt(task_id: UUID, run_id: UUID | None, error: str) -> str:
    with scoped_session(owner=True) as s:
        task = s.get(Task, task_id)
        if run_id:
            run = s.get(TaskRun, run_id)
            run.status, run.error, run.finished_at = "failed", error[:4000], datetime.now(UTC)
        status = lifecycle.schedule_retry(s, task, error)
        audit(s, actor_type="system", actor="runner", action="task.attempt_failed", target_type="task",
              target_id=task.id, client_id=task.client_id, outcome="error",
              data={"attempt": task.attempts, "error": error[:1000]})
        return status

