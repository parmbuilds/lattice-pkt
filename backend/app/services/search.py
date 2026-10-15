"""Search (R19, R21): one SQL statement with the policy filter before ranking and LIMIT (KTD9)."""

from uuid import UUID

from sqlalchemy import and_, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentVersion, User, VersionDecision
from app.policy import Action, Subject, read_clause

MAX_QUERY = 1000
# Plain-text match markers, never HTML (KTD9, KTD11).
HEADLINE_OPTIONS = "StartSel=[[, StopSel=]], MaxFragments=2, MaxWords=20, MinWords=5"


async def search(db: AsyncSession, subject: Subject, query: str, limit: int) -> list[dict]:
    query = query.strip()[:MAX_QUERY]
    if not query:
        return []
    d = Document.__table__
    v = DocumentVersion.__table__
    tsquery = func.websearch_to_tsquery("english", query)  # any text is valid input
    rank = func.ts_rank(v.c.search_vector, tsquery).label("rank")
    matches = (
        select(v.c.id, v.c.document_id, v.c.title, v.c.body, rank)
        .select_from(v.join(d, v.c.document_id == d.c.id))
        .where(
            read_clause(subject, Action.RETRIEVE, d, v),
            v.c.search_vector.op("@@")(tsquery),
        )
        .order_by(rank.desc(), v.c.id)
        .limit(limit)
        .subquery()
    )
    # Snippets and approver details are computed for the returned rows only.
    approval = VersionDecision.__table__
    rows = await db.execute(
        select(
            matches.c.id,
            matches.c.document_id,
            matches.c.title,
            matches.c.rank,
            func.ts_headline(
                "english", matches.c.body, tsquery, literal_column(f"'{HEADLINE_OPTIONS}'")
            ),
            approval.c.created_at,
            User.display_name,
        )
        .select_from(
            matches.outerjoin(
                approval,
                and_(approval.c.version_id == matches.c.id, approval.c.decision == "approved"),
            ).outerjoin(User, User.id == approval.c.actor_id)
        )
        .order_by(matches.c.rank.desc(), matches.c.id)
    )
    return [
        {
            "version_id": str(vid),
            "document_id": str(did),
            "title": title,
            "rank": round(float(score), 6),
            "snippet": snippet,
            "approved_at": approved_at.isoformat() if approved_at else None,
            "approved_by": approver,
        }
        for vid, did, title, score, snippet, approved_at, approver in rows
    ]


def returned_ids(results: list[dict]) -> list[UUID]:
    return [UUID(r["version_id"]) for r in results]
