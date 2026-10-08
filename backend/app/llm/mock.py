"""Offline stand-in for Claude (LLM_PROVIDER=mock).

It lets the whole system - planning, approvals, delegation, review, chat and reports - run end
to end without an API key. It is deliberately simple and *honest*: specialist results say that
no real work was done in mock mode, so mock output can never be mistaken for real results.
"""

import json
import itertools
from typing import Any

from app.llm.gateway import LLMRequest, LLMResponse

_ids = itertools.count(1)


def _tool(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_mock_{next(_ids):06d}", "name": name, "input": payload}


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _last_results(messages: list[dict]) -> list[dict]:
    """Parsed JSON of the tool results in the most recent user turn."""
    if not messages or messages[-1]["role"] != "user" or not isinstance(messages[-1]["content"], list):
        return []
    out = []
    for block in messages[-1]["content"]:
        if block.get("type") == "tool_result":
            try:
                out.append(json.loads(block["content"]))
            except (TypeError, ValueError):
                out.append({})
    return out


def _submit(status: str, summary: str, output: dict | None = None) -> dict:
    return _tool("submit_result", {"status": status, "summary": summary, "output": output or {},
                                   "confidence": 0.5})


class MockProvider:
    def complete(self, req: LLMRequest) -> LLMResponse:
        turn = sum(1 for m in req.messages if m["role"] == "assistant")
        meta = req.meta
        if meta.get("agent_template") == "manager":
            blocks = self._manager(meta, turn, req.messages)
        else:
            blocks = self._specialist(meta, turn)
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        return LLMResponse(content=blocks, stop_reason=stop, model="mock", input_tokens=0, output_tokens=0)

    # ------------------------------------------------------------------ manager

    def _manager(self, meta: dict, turn: int, messages: list[dict]) -> list[dict]:
        kind = meta.get("task_kind")
        task = meta.get("task", {})
        if kind == "review":
            if turn == 0:
                return [_tool("review_task", {"task_id": task["inputs"]["task_id"], "verdict": "approve",
                                              "score": 6, "feedback": "Mock review: accepted as a placeholder."})]
            return [_submit("success", "Reviewed the submitted work (mock mode).")]

        if kind == "chat":
            if turn == 0:
                return [_tool("get_agency_status", {})]
            if turn == 1:
                status = (_last_results(messages) or [{}])[0]
                return [_tool("reply_to_owner", {"message": _chat_reply(meta.get("owner_message", ""), status)})]
            return [_submit("success", "Answered the owner.")]

        if kind == "report":
            return [_submit("success", "Report written.", {"narrative": _report_narrative(task.get("inputs", {}))})]

        if kind == "checkin":
            events = task.get("inputs", {}).get("events", [])
            return [_submit("success", f"Noted {len(events)} update(s); no further action in mock mode.")]

        # Planning a goal.
        if turn == 0:
            return [_tool("get_agency_status", {})]
        if turn == 1:
            status = (_last_results(messages) or [{}])[0]
            goal = next(iter(status.get("goals", [])), None) or {}
            roles = {a["role"] for a in status.get("agents", []) if a.get("status") == "active"}
            calls = []
            if goal.get("id"):
                calls.append(_tool("set_goal_strategy", {
                    "goal_id": goal["id"],
                    "summary": "Start with websites for local businesses that have weak or no sites; "
                               "research leads, then personalised outreach.",
                    "focus_services": goal.get("priority_services") or ["website"],
                    "target_segments": ["gyms and fitness studios", "salons and spas"],
                    "channels": ["email outreach"],
                    "weekly_targets": {"leads_researched": 50, "outreach_drafted": 30},
                }))
            for role in ("lead_research", "sales"):
                if role not in roles:
                    calls.append(_tool("propose_agent", {"role": role, "reason": f"No {role} agent exists yet; "
                                                         "it is needed to start the revenue pipeline."}))
            return calls or [_text("Nothing to change.")]
        if turn == 2:
            return [_tool("create_task", {
                "title": "Research 20 local businesses that need a new website",
                "description": "Find gyms, fitness studios and salons in the US/UK whose website is slow, "
                               "outdated or missing. Score each lead and record specific reasons with evidence.",
                "role": "lead_research",
                "acceptance_criteria": ["20 leads saved with lead_upsert", "Each lead has 2+ evidence-backed reasons"],
                "priority": 2,
            })]
        if turn == 3:
            first = (_last_results(messages) or [{}])[0]
            deps = [first["task_id"]] if first.get("task_id") else []
            return [_tool("create_task", {
                "title": "Prepare personalised outreach for the top-scored leads",
                "description": "For leads scoring 60+, plan personalised outreach referencing their evidence.",
                "role": "sales",
                "acceptance_criteria": ["Every message cites the lead's own evidence", "Opt-out line included"],
                "priority": 3,
                "depends_on": deps,
            })]
        if turn == 4:
            return [_tool("reply_to_owner", {"message": (
                "I've set up the plan for your goal. I've asked for two new agents (Lead Research and Sales) - "
                "they're waiting for your approval in the Approvals page. Once approved, Lead Research starts "
                "finding businesses and Sales prepares outreach for the best ones.\n\n"
                "Heads-up: I'm running in offline mock mode, so no real research happens until an Anthropic "
                "API key is configured.")})]
        return [_submit("success", "Strategy written, agents proposed and first tasks created.")]

    # ------------------------------------------------------------------ specialists

    def _specialist(self, meta: dict, turn: int) -> list[dict]:
        return [_submit(
            "partial",
            "Offline mock mode: no real work was performed. Configure ANTHROPIC_API_KEY (and the research "
            "connectors in Phase 2) for real results.",
            {"mock": True},
        )]


def _chat_reply(question: str, status: dict) -> str:
    tasks = status.get("tasks_by_status", {})
    return (
        f"You asked: \"{question[:200]}\"\n\n"
        f"Here's where we stand: revenue this month is {status.get('revenue_this_month', 0):.2f}, "
        f"{sum(tasks.values()) if tasks else 0} tasks in the system "
        f"({tasks.get('completed', 0)} completed, {tasks.get('failed', 0)} failed), "
        f"{status.get('pending_approvals', 0)} approvals waiting for you and "
        f"{status.get('open_human_tasks', 0)} steps only you can do.\n\n"
        "I'm in offline mock mode, so I can report numbers but can't reason about them until an "
        "Anthropic API key is configured."
    )


def _report_narrative(data: dict) -> str:
    t = data.get("tasks", {})
    lines = [
        "Here's my report for this period.",
        "",
        f"- Revenue: {data.get('revenue', 0):,.2f} {data.get('currency', 'USD')} (goal: "
        + (f"{data['goal_target']:,.0f}" if data.get("goal_target") else "not set") + ").",
        f"- Tasks: {t.get('completed', 0)} completed, {t.get('failed', 0)} failed, {t.get('open', 0)} still open.",
        f"- New leads: {data.get('new_leads', 0)}.",
        f"- Waiting on you: {data.get('pending_approvals', 0)} approvals and {data.get('open_human_tasks', 0)} "
        "human steps.",
    ]
    if data.get("errors"):
        lines.append(f"- Problems: {data['errors']} errors were logged - see the Errors page.")
    lines += ["", "I'm running in offline mock mode, so this report is numbers only."]
    return "\n".join(lines)
