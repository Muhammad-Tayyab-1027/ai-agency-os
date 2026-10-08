"""Lead / pipeline tools."""

from typing import Literal
from uuid import UUID

from pydantic import Field, HttpUrl
from sqlalchemy import func, or_, select

from app.db.enums import PIPELINE_STAGES, TIER_AUTOMATIC
from app.db.models import Lead, LeadEvent, Service, SuppressionEntry
from app.tools.base import ToolContext, ToolError, ToolInput, tool

Stage = Literal[PIPELINE_STAGES]  # type: ignore[valid-type]


def lead_dict(lead: Lead) -> dict:
    return {
        "id": str(lead.id), "business_name": lead.business_name, "website": lead.website, "email": lead.email,
        "phone": lead.phone, "city": lead.city, "country": lead.country, "category": lead.category,
        "stage": lead.stage, "score": lead.score, "score_reasons": lead.score_reasons,
        "recommended_service": lead.recommended_service, "evidence": lead.evidence, "opted_out": lead.opted_out,
        "source_url": lead.source_url, "notes": lead.notes,
    }


def is_suppressed(session, email: str | None, website: str | None) -> bool:
    values = []
    if email:
        values += [email.lower(), "@" + email.lower().split("@")[-1]]
    if website:
        host = website.lower().split("//")[-1].split("/")[0].removeprefix("www.")
        values.append("@" + host)
    if not values:
        return False
    return session.execute(select(SuppressionEntry.value).where(SuppressionEntry.value.in_(values))).first() is not None


class LeadListInput(ToolInput):
    stage: Stage | None = None
    min_score: int | None = Field(default=None, ge=0, le=100)
    search: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=25, ge=1, le=100)


@tool(
    "lead_list",
    description="List leads, optionally filtered by pipeline stage, minimum score or a name search.",
    input_model=LeadListInput,
    tier=TIER_AUTOMATIC,
    scopes={"leads:read"},
)
def lead_list(ctx: ToolContext, p: LeadListInput) -> dict:
    q = select(Lead).order_by(Lead.score.desc().nulls_last(), Lead.created_at.desc()).limit(p.limit)
    if p.stage:
        q = q.where(Lead.stage == p.stage)
    if p.min_score is not None:
        q = q.where(Lead.score >= p.min_score)
    if p.search:
        q = q.where(or_(Lead.business_name.ilike(f"%{p.search}%"), Lead.category.ilike(f"%{p.search}%")))
    leads = ctx.session.execute(q).scalars().all()
    total = ctx.session.execute(select(func.count()).select_from(Lead)).scalar_one()
    return {"total_leads": total, "leads": [lead_dict(lead) for lead in leads]}


class Reason(ToolInput):
    reason: str = Field(min_length=10, max_length=500)
    evidence_url: HttpUrl


class LeadUpsertInput(ToolInput):
    business_name: str = Field(min_length=2, max_length=300)
    source: str = Field(min_length=2, max_length=120, description="Where the lead was found, e.g. 'Google Maps'.")
    source_url: HttpUrl
    website: HttpUrl | None = None
    email: str | None = Field(default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=50)
    contact_name: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, min_length=2, max_length=2, description="ISO country code")
    category: str | None = Field(default=None, max_length=120)
    score: int = Field(ge=0, le=100)
    reasons: list[Reason] = Field(min_length=2, max_length=10)
    recommended_service: str
    notes: str | None = Field(default=None, max_length=4000)


@tool(
    "lead_upsert",
    description=(
        "Create or update a researched lead. Requires a score and at least two specific reasons, each with an "
        "evidence URL. Leads on the suppression list are refused."
    ),
    input_model=LeadUpsertInput,
    tier=TIER_AUTOMATIC,
    scopes={"leads:write"},
    daily_limit=300,
)
def lead_upsert(ctx: ToolContext, p: LeadUpsertInput) -> dict:
    s = ctx.session
    website = str(p.website) if p.website else None
    if is_suppressed(s, p.email, website):
        raise ToolError("This business is on the suppression list and must not be added.")
    if s.get(Service, p.recommended_service) is None:
        keys = s.execute(select(Service.key).where(Service.active)).scalars().all()
        raise ToolError(f"Unknown service '{p.recommended_service}'. Valid: {keys}.")
    lead = s.execute(
        select(Lead).where(func.lower(Lead.business_name) == p.business_name.lower(),
                           Lead.website.is_not_distinct_from(website))
    ).scalar_one_or_none()
    created = lead is None
    if created:
        lead = Lead(business_name=p.business_name, website=website, source=p.source, created_by=ctx.agent.key)
        s.add(lead)
    elif lead.opted_out:
        raise ToolError("This lead opted out and must not be updated for outreach.")
    lead.source = p.source
    lead.source_url = str(p.source_url)
    for field in ("email", "phone", "contact_name", "city", "category", "notes"):
        value = getattr(p, field)
        if value is not None:
            setattr(lead, field, value)
    if p.country:
        lead.country = p.country.upper()
    lead.score = p.score
    lead.score_reasons = [r.reason for r in p.reasons]
    lead.evidence = [str(r.evidence_url) for r in p.reasons]
    lead.recommended_service = p.recommended_service
    s.flush()
    if created:
        s.add(LeadEvent(lead_id=lead.id, from_stage=None, to_stage=lead.stage, actor=ctx.agent.key, note="created"))
    return {"lead_id": str(lead.id), "created": created}


class StageInput(ToolInput):
    lead_id: UUID
    stage: Stage
    note: str = Field(min_length=3, max_length=2000)


# Moves an agent may make on its own; won/lost are confirmed by the owner in the dashboard.
AGENT_FORBIDDEN_STAGES = {"won"}


@tool(
    "lead_update_stage",
    description="Move a lead to another pipeline stage with a note. Only the owner can mark a lead 'won'.",
    input_model=StageInput,
    tier=TIER_AUTOMATIC,
    scopes={"leads:write"},
    daily_limit=300,
)
def lead_update_stage(ctx: ToolContext, p: StageInput) -> dict:
    lead = ctx.session.get(Lead, p.lead_id)
    if lead is None:
        raise ToolError("Lead not found.")
    if p.stage in AGENT_FORBIDDEN_STAGES:
        raise ToolError("Only the owner can mark a deal as won. Use request_human_action to hand it over.")
    if lead.opted_out and p.stage not in ("lost",):
        raise ToolError("This lead opted out; it can only be moved to 'lost'.")
    previous = lead.stage
    lead.stage = p.stage
    ctx.session.add(LeadEvent(lead_id=lead.id, from_stage=previous, to_stage=p.stage, actor=ctx.agent.key,
                              note=p.note))
    ctx.session.flush()
    return {"lead_id": str(lead.id), "from": previous, "to": p.stage}
