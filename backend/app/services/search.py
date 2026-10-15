"""Hybrid search (R15-R19): one SQL statement, the read policy before ranking and LIMIT (KTD6-7)."""

import logging
import re
from uuid import UUID

from sqlalchemy import and_, case, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.embedding import Embedder
from app.models import Chunk, Document, DocumentVersion, User, VersionDecision
from app.policy import Action, Subject, read_clause
from app.services.derive import config_hash

log = logging.getLogger("lattice.search")

MAX_QUERY = 1000
# Plain-text match markers, never HTML (KTD9, KTD11).
HEADLINE_OPTIONS = "StartSel=[[, StopSel=]], MaxFragments=2, MaxWords=20, MinWords=5"
RRF_K = 60  # the customary reciprocal-rank-fusion constant
MAX_SPANS = 3
SNIPPET_CHARS = 200
# Breaks up "[[" and "]]" in a best-span snippet so body text cannot pose as a match marker.
_MARKER = re.compile(r"([\[\]])(?=\1)")


def _vector_candidates(subject: Subject, distance, current_hash: str, settings: Settings):
    """Readable versions ranked by their nearest chunk, capped after the policy filter (KTD6)."""
    d, v, c = Document.__table__, DocumentVersion.__table__, Chunk.__table__
    best = func.min(distance)
    return (
        select(
            c.c.version_id, func.row_number().over(order_by=(best, c.c.version_id)).label("rank")
        )
        .select_from(c.join(v, v.c.id == c.c.version_id).join(d, v.c.document_id == d.c.id))
        .where(
            read_clause(subject, Action.RETRIEVE, d, v),
            # Redundant with read_clause, but lets the planner skip other workspaces' chunks
            # before computing any distance.
            c.c.workspace_id == subject.workspace_id,
            c.c.config_hash == current_hash,
            distance < settings.vector_distance_floor,
        )
        .group_by(c.c.version_id)
        .order_by(best, c.c.version_id)
        .limit(settings.vector_candidates)
        .subquery("nearest")
    )


async def search(
    db: AsyncSession,
    subject: Subject,
    query: str,
    limit: int,
    embedder: Embedder,
    settings: Settings,
) -> list[dict]:
    query = query.strip()[:MAX_QUERY]
    if not query:
        return []
    try:
        query_vector = await embedder.embed_query(query)
    except Exception:
        log.warning("query embedding failed; answering from keywords only", exc_info=True)
        query_vector = None  # every distance is NULL, so no chunk passes the floor (R19)
    d, v, c = Document.__table__, DocumentVersion.__table__, Chunk.__table__
    current = config_hash(embedder)
    tsquery = func.websearch_to_tsquery("english", query)  # any text is valid input
    distance = c.c.embedding.cosine_distance(query_vector)

    ts_rank = func.ts_rank(v.c.search_vector, tsquery)
    lexical = (
        select(
            v.c.id.label("version_id"),
            func.row_number().over(order_by=(ts_rank.desc(), v.c.id)).label("rank"),
        )
        .select_from(v.join(d, v.c.document_id == d.c.id))
        .where(read_clause(subject, Action.RETRIEVE, d, v), v.c.search_vector.op("@@")(tsquery))
        .subquery("lexical")
    )
    nearest = _vector_candidates(subject, distance, current, settings)
    version_id = func.coalesce(lexical.c.version_id, nearest.c.version_id).label("version_id")
    score = (
        func.coalesce(1.0 / (RRF_K + lexical.c.rank), 0.0)
        + func.coalesce(1.0 / (RRF_K + nearest.c.rank), 0.0)
    ).label("score")
    fused = (
        select(version_id, score, lexical.c.version_id.is_not(None).label("lexical_hit"))
        .select_from(
            lexical.outerjoin(nearest, lexical.c.version_id == nearest.c.version_id, full=True)
        )
        .order_by(score.desc(), version_id)
        .limit(limit)
        .cte("fused")
    )

    # Evidence spans for the returned rows only: chunks near the query or holding a keyword.
    chunk_text = func.substr(v.c.body, c.c.start + 1, c.c.end - c.c.start)
    ranked = (
        select(
            c.c.version_id,
            func.jsonb_build_array(c.c.start, c.c.end, c.c.sha256).label("span"),
            c.c.start,
            c.c.end,
            func.row_number()
            .over(partition_by=c.c.version_id, order_by=(distance.nulls_last(), c.c.ordinal))
            .label("n"),
        )
        .select_from(
            c.join(fused, fused.c.version_id == c.c.version_id).join(v, v.c.id == c.c.version_id)
        )
        .where(
            c.c.config_hash == current,
            or_(
                distance < settings.vector_distance_floor,
                func.to_tsvector("english", chunk_text).op("@@")(tsquery),
            ),
        )
        .subquery("ranked")
    )
    first = ranked.c.n == 1
    spans = (
        select(
            ranked.c.version_id,
            func.jsonb_agg(aggregate_order_by(ranked.c.span, ranked.c.n)).label("spans"),
            func.min(ranked.c.start).filter(first).label("best_start"),
            func.min(ranked.c.end).filter(first).label("best_end"),
        )
        .where(ranked.c.n <= MAX_SPANS)
        .group_by(ranked.c.version_id)
        .subquery("spans")
    )

    # A keyword hit gets ts_headline; a meaning-only hit gets the start of its best span (R16).
    headline = func.ts_headline(
        "english", v.c.body, tsquery, literal_column(f"'{HEADLINE_OPTIONS}'")
    )
    best_span = func.substr(
        v.c.body,
        spans.c.best_start + 1,
        func.least(spans.c.best_end - spans.c.best_start, SNIPPET_CHARS),
    )
    approval = VersionDecision.__table__
    rows = await db.execute(
        select(
            fused.c.version_id,
            v.c.document_id,
            v.c.title,
            fused.c.score,
            fused.c.lexical_hit,
            case((fused.c.lexical_hit, headline), else_=best_span),
            spans.c.spans,
            approval.c.created_at,
            User.display_name,
        )
        .select_from(
            fused.join(v, v.c.id == fused.c.version_id)
            .outerjoin(spans, spans.c.version_id == fused.c.version_id)
            .outerjoin(
                approval,
                and_(
                    approval.c.version_id == fused.c.version_id, approval.c.decision == "approved"
                ),
            )
            .outerjoin(User, User.id == approval.c.actor_id)
        )
        .order_by(fused.c.score.desc(), fused.c.version_id)
    )
    retrieval = "lexical" if query_vector is None else "hybrid"
    return [
        {
            "version_id": str(vid),
            "document_id": str(did),
            "title": title,
            "rank": round(float(score), 6),
            "snippet": snippet if lexical_hit else _MARKER.sub(r"\1 ", snippet),
            "spans": [{"start": s, "end": e, "sha256": h} for s, e, h in found or []],
            "retrieval": retrieval,
            "approved_at": approved_at.isoformat() if approved_at else None,
            "approved_by": approver,
        }
        for vid, did, title, score, lexical_hit, snippet, found, approved_at, approver in rows
    ]


def returned_ids(results: list[dict]) -> list[UUID]:
    return [UUID(r["version_id"]) for r in results]


def returned_spans(results: list[dict]) -> list[list]:
    """Audit form of the evidence handed out: [version_id, start, end] triples (KTD16)."""
    return [[r["version_id"], s["start"], s["end"]] for r in results for s in r["spans"]]
