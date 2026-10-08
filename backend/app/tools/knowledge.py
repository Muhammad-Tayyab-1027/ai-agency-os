"""Knowledge base tools. Client-specific documents are isolated by RLS: an agent working on a
client's task can only read that client's documents plus agency-global ones."""

from pydantic import Field
from sqlalchemy import func, literal_column, select

from app.db.enums import TIER_AUTOMATIC
from app.db.models import KnowledgeDoc
from app.tools.base import ToolContext, ToolInput, tool


class KbSearchInput(ToolInput):
    query: str = Field(min_length=2, max_length=300)
    limit: int = Field(default=5, ge=1, le=20)


@tool(
    "kb_search",
    description="Search the agency knowledge base (playbooks, learnings, client notes) by keywords.",
    input_model=KbSearchInput,
    tier=TIER_AUTOMATIC,
    scopes={"kb:read"},
)
def kb_search(ctx: ToolContext, p: KbSearchInput) -> dict:
    doc = func.to_tsvector(literal_column("'english'"), KnowledgeDoc.title + " " + KnowledgeDoc.content)
    query = func.websearch_to_tsquery(literal_column("'english'"), p.query)
    rank = func.ts_rank(doc, query)
    rows = ctx.session.execute(
        select(KnowledgeDoc, rank.label("rank")).where(doc.op("@@")(query)).order_by(rank.desc()).limit(p.limit)
    ).all()
    return {
        "results": [
            {"id": str(d.id), "title": d.title, "content": d.content[:2000], "tags": d.tags,
             "client_specific": d.client_id is not None, "source": d.source}
            for d, _ in rows
        ]
    }


class KbWriteInput(ToolInput):
    title: str = Field(min_length=3, max_length=300)
    content: str = Field(min_length=10, max_length=20000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    source: str | None = Field(default=None, max_length=500)
    client_specific: bool = Field(
        default=False, description="Store under the current task's client (only visible for that client)."
    )


@tool(
    "kb_write",
    description="Save a learning, playbook or note to the knowledge base.",
    input_model=KbWriteInput,
    tier=TIER_AUTOMATIC,
    scopes={"kb:write"},
    daily_limit=100,
)
def kb_write(ctx: ToolContext, p: KbWriteInput) -> dict:
    doc = KnowledgeDoc(
        client_id=ctx.client_id if p.client_specific else None,
        title=p.title,
        content=p.content,
        tags=p.tags,
        source=p.source,
        created_by=ctx.agent.key,
    )
    ctx.session.add(doc)
    ctx.session.flush()
    return {"id": str(doc.id), "saved": True}
