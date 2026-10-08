"""Allowed values for status-like string columns."""

AGENT_STATUSES = ("active", "paused", "retired")

TASK_STATUSES = (
    "queued",  # ready to run once dependencies are done
    "running",
    "in_review",  # output submitted, waiting for the manager's review
    "blocked",  # waiting on a human task or a missing capability
    "completed",
    "failed",
    "cancelled",
)
TASK_OPEN_STATUSES = ("queued", "running", "in_review", "blocked")

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "executed", "failed")

PIPELINE_STAGES = (
    "new_lead",
    "researching",
    "contacted",
    "responded",
    "qualified",
    "proposal_sent",
    "negotiation",
    "won",
    "lost",
    "follow_up",
)

USER_ROLES = ("owner", "operator", "viewer")

# Autonomy tiers (see docs/ARCHITECTURE.md)
TIER_AUTOMATIC = "A"
TIER_APPROVAL = "B"
TIER_NEVER = "C"
TIER_ORDER = {TIER_AUTOMATIC: 0, TIER_APPROVAL: 1, TIER_NEVER: 2}
