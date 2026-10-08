"""Runtime configuration. Every secret comes from environment variables (or a .env file
that is never committed). Nothing in this module should ever be logged verbatim."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- core -----------------------------------------------------------------
    environment: str = "development"
    database_url: str = "postgresql+psycopg://agency:agency@localhost:5432/agency"
    secret_key: SecretStr = SecretStr("change-me-in-production")
    session_ttl_hours: int = 12
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- LLM ------------------------------------------------------------------
    # "anthropic" uses the Claude API (needs ANTHROPIC_API_KEY). "mock" runs a
    # deterministic offline stand-in so the whole system works without keys.
    llm_provider: str = "mock"
    anthropic_api_key: SecretStr | None = None
    manager_model: str = "claude-opus-5-5"
    specialist_model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-5-5"
    llm_max_tokens: int = 16000
    agent_max_turns: int = 25  # LLM round-trips per task run

    # --- guardrails -----------------------------------------------------------
    max_agents: int = 10
    daily_llm_budget_usd: float = 20.0
    default_agent_daily_budget_usd: float = 5.0
    max_open_tasks: int = 60
    max_task_depth: int = 5
    task_max_attempts: int = 3
    task_max_revisions: int = 2
    approval_ttl_hours: int = 72
    outreach_daily_cap: int = 30

    # --- worker ---------------------------------------------------------------
    worker_poll_seconds: float = 2.0
    task_lock_timeout_minutes: int = 30
    daily_report_hour_utc: int = 7

    # --- agency profile (used in prompts) -------------------------------------
    agency_name: str = "Agency OS"
    target_markets: list[str] = Field(default_factory=lambda: ["US", "UK"])
    target_niches: list[str] = Field(
        default_factory=lambda: [
            "gyms and fitness studios",
            "salons and spas",
            "dental and medical clinics",
            "restaurants and cafes",
            "home services and trades",
        ]
    )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
