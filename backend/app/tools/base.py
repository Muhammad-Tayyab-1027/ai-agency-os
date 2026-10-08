"""Tool registry.

A tool is ordinary Python code with a typed input model, a risk tier and the permission
scopes it needs. Agents never execute tools directly: every call goes through
``app.policy.engine.invoke`` which enforces scopes, tiers, rate limits and auditing.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db.models import Agent, Task


class ToolInput(BaseModel):
    """Base for tool inputs: unknown fields are rejected (input validation)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass
class ToolContext:
    session: Session
    agent: Agent
    task: Task | None = None
    run_id: UUID | None = None
    approved: bool = False  # True when executing an owner-approved snapshot
    approval_id: UUID | None = None
    # Side effects the runner should apply after the turn (e.g. block the task).
    effects: dict[str, Any] = field(default_factory=dict)

    @property
    def client_id(self) -> UUID | None:
        return self.task.client_id if self.task else None

    @property
    def actor(self) -> str:
        return self.agent.key


class ToolError(Exception):
    """A user-facing tool failure; the message is returned to the agent as is_error."""


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]
    tier: str
    scopes: frozenset[str]
    handler: Callable[[ToolContext, Any], dict]
    daily_limit: int | None = None
    approval_title: Callable[[Any], str] | None = None
    # Validation run *before* an approval is queued, so the owner never sees doomed requests.
    precheck: Callable[[ToolContext, Any], None] | None = None

    def anthropic_schema(self) -> dict:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}


REGISTRY: dict[str, ToolSpec] = {}


def tool(
    name: str,
    *,
    description: str,
    input_model: type[ToolInput],
    tier: str,
    scopes: set[str],
    daily_limit: int | None = None,
    approval_title: Callable[[Any], str] | None = None,
    precheck: Callable[[ToolContext, Any], None] | None = None,
):
    def decorator(fn: Callable[[ToolContext, Any], dict]):
        if name in REGISTRY:
            raise RuntimeError(f"duplicate tool {name}")
        REGISTRY[name] = ToolSpec(
            name=name,
            description=description.strip(),
            input_model=input_model,
            tier=tier,
            scopes=frozenset(scopes),
            handler=fn,
            daily_limit=daily_limit,
            approval_title=approval_title,
            precheck=precheck,
        )
        return fn

    return decorator


def load_tools() -> dict[str, ToolSpec]:
    """Import every tool module so their decorators register."""
    from app.tools import agents_admin, crm, human, knowledge, orchestration  # noqa: F401

    return REGISTRY
