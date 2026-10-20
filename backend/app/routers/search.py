from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import Uuid, and_, or_, select

from app.ai import retention
from app.audit import authorize, record
from app.auth import Member
from app.db import DB
from app.models import AccessToken, AuditEvent, Conversation
from app.policy import Action
from app.services.search import returned_ids, returned_spans, search

router = APIRouter(prefix="/api/workspaces/{workspace_id}/search", tags=["search"])

HISTORY_LIMIT = 10


@router.get("")
async def search_documents(
    subject: Member,
    db: DB,
    request: Request,
    q: str = "",
    limit: int = Query(default=10, ge=1),
    via: Literal["search", "ask"] = "search",
) -> dict:
    app = request.app.state
    cap = app.settings.search_limit_cap
    decision = await authorize(db, subject, Action.SEARCH, detail={"query": q}, defer_allowed=True)
    results = await search(db, subject, q, min(limit, cap), app.embedder, app.settings)
    # The audit row lists exactly what was returned, committed before the response (R28).
    await record(
        db,
        subject,
        decision,
        returned_version_ids=returned_ids(results),
        detail={
            "query": q,
            "limit": min(limit, cap),
            "count": len(results),
            "returned_spans": returned_spans(results),
        }
        # Marks questions asked on the Overview so the person's history can label them.
        | ({"via": "ask"} if via == "ask" else {}),
    )
    return {"results": results}


@router.get("/history")
async def own_searches(subject: Member, db: DB) -> dict:
    """This person's latest searches here: from Search, Ask and their own agents' tokens."""
    await authorize(db, subject, Action.VIEW_OWN_SEARCHES)
    # shortcut: walks the trail newest first; add an (actor, action) index if it grows large.
    rows = await db.execute(
        select(
            AuditEvent.occurred_at,
            AuditEvent.channel,
            AuditEvent.detail,
            AccessToken.name,
            Conversation.id,
        )
        .outerjoin(AccessToken, AccessToken.id == AuditEvent.token_id)
        .outerjoin(
            Conversation,
            and_(
                AuditEvent.action == Action.ASK,
                Conversation.id == AuditEvent.detail["conversation_id"].astext.cast(Uuid),
                *await retention.own_unexpired(db, subject),
            ),
        )
        .where(
            AuditEvent.workspace_id == subject.workspace_id,
            AuditEvent.actor_user_id == subject.user_id,
            AuditEvent.outcome == "allow",
            or_(
                AuditEvent.action == Action.SEARCH,
                # A turn's "sent" row, while its conversation lives (KTD13); outcome rows
                # carry no query.
                and_(Conversation.id.is_not(None), AuditEvent.detail.has_key("query")),
            ),
        )
        .order_by(AuditEvent.id.desc())
        .limit(HISTORY_LIMIT)
    )
    return {"searches": [_searched(*row) for row in rows]}


def _searched(
    occurred_at,
    channel: str,
    detail: dict | None,
    token_name: str | None,
    conversation_id: UUID | None,
) -> dict:
    detail = detail or {}
    source = "ask" if conversation_id else detail.get("via", "search")
    return {
        "occurred_at": occurred_at.isoformat(),
        "source": "agent" if channel == "mcp" else source,
        "conversation_id": str(conversation_id) if conversation_id else None,
        "token_name": token_name,
        "query": detail.get("query", ""),
        "count": detail.get("count", 0),
    }
