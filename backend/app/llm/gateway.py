"""LLM gateway: one interface over the Claude API and an offline mock.

The runner owns the agent loop (not the SDK tool runner) because every tool call must pass the
policy engine, be persisted, and be able to stop the run when work is queued for approval.
Conversation history is append-only: assistant content blocks are stored and replayed exactly
as returned, which keeps thinking blocks valid across turns.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from app.config import get_settings

# USD per 1M tokens: (input, output, cache read)
PRICING: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "mock": (0.0, 0.0, 0.0),
}
# Models that accept server-side refusal fallbacks ("default" routing).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


@dataclass
class LLMRequest:
    model: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    effort: str = "medium"
    max_tokens: int = 16000
    # Context for the offline mock only (never sent to the API).
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: list[dict[str, Any]]
    stop_reason: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    refusal_category: str | None = None

    @property
    def cost_usd(self) -> float:
        p_in, p_out, p_cache = PRICING.get(self.model, PRICING["claude-opus-5-5"])
        return (self.input_tokens * p_in + self.output_tokens * p_out + self.cache_read_tokens * p_cache) / 1e6

    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text").strip()


class LLMError(Exception):
    """Retryable or not, the run fails honestly and the task's retry policy applies."""


class Provider(Protocol):
    def complete(self, req: LLMRequest) -> LLMResponse: ...


class AnthropicProvider:
    def __init__(self) -> None:
        settings = get_settings()
        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        # With no explicit key the SDK resolves credentials from the environment.
        self.client = anthropic.Anthropic(api_key=key, max_retries=3)

    def complete(self, req: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            # Stable system prompt first so it is cached across turns and tasks.
            "system": [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
            "messages": req.messages,
            "tools": req.tools,
            "output_config": {"effort": req.effort},
        }
        if req.model in FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        try:
            with self.client.beta.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic API key is missing or invalid (ANTHROPIC_API_KEY).") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit hit; will retry later.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API.") from exc

        usage = msg.usage
        category = None
        if msg.stop_reason == "refusal" and getattr(msg, "stop_details", None):
            category = getattr(msg.stop_details, "category", None)
        return LLMResponse(
            content=[block.to_dict() for block in msg.content],
            stop_reason=msg.stop_reason or "end_turn",
            model=msg.model,
            input_tokens=(usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            refusal_category=category,
        )


_provider_override: Provider | None = None


def set_provider(provider: Provider | None) -> None:
    """Tests inject a scripted provider here."""
    global _provider_override
    _provider_override = provider


def get_provider() -> Provider:
    if _provider_override is not None:
        return _provider_override
    if get_settings().llm_provider == "anthropic":
        return AnthropicProvider()
    from app.llm.mock import MockProvider

    return MockProvider()
