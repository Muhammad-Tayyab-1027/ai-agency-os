"""Ask the owner to do something only a human may do (Level C steps, sign-ups, keys, closing deals)."""

from pydantic import Field

from app.core.audit import audit
from app.db.enums import TIER_AUTOMATIC
from app.db.models import HumanTask
from app.tools.base import ToolContext, ToolInput, tool


class HumanActionInput(ToolInput):
    title: str = Field(min_length=5, max_length=300)
    reason: str = Field(min_length=10, max_length=2000, description="Why this needs the owner.")
    steps: list[str] = Field(
        min_length=1, max_length=25, description="Exact, numbered-in-order steps the owner should follow."
    )
    blocks_current_task: bool = Field(
        default=False, description="True if this task cannot continue until the owner finishes."
    )


@tool(
    "request_human_action",
    description=(
        "Ask the owner to perform a step only a human may do: create an account, get an API key, verify "
        "identity, sign, pay, or close a qualified deal. Give exact step-by-step instructions. Never ask for "
        "passwords or keys to be pasted into chat; keys go into the server's environment variables."
    ),
    input_model=HumanActionInput,
    tier=TIER_AUTOMATIC,
    scopes={"human:request"},
    daily_limit=30,
)
def request_human_action(ctx: ToolContext, p: HumanActionInput) -> dict:
    human = HumanTask(
        title=p.title,
        reason=p.reason,
        steps=p.steps,
        created_by=ctx.agent.key,
        blocks_task_id=ctx.task.id if (ctx.task and p.blocks_current_task) else None,
        client_id=ctx.client_id,
    )
    ctx.session.add(human)
    ctx.session.flush()
    audit(ctx.session, actor_type="agent", actor=ctx.actor, action="human_task.requested",
          target_type="human_task", target_id=human.id, client_id=ctx.client_id, data={"title": p.title})
    if p.blocks_current_task:
        ctx.effects["blocked_by_human_task"] = str(human.id)
    return {
        "human_task_id": str(human.id),
        "message": "The owner has been asked. Finish this task with status 'needs_human' if you cannot continue."
        if p.blocks_current_task
        else "The owner has been asked.",
    }
