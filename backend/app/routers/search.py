from fastapi import APIRouter, Query, Request

from app.audit import authorize, record
from app.auth import Member
from app.db import DB
from app.policy import Action
from app.services.search import returned_ids, search

router = APIRouter(prefix="/api/workspaces/{workspace_id}/search", tags=["search"])


@router.get("")
async def search_documents(
    subject: Member, db: DB, request: Request, q: str = "", limit: int = Query(default=10, ge=1)
) -> dict:
    cap = request.app.state.settings.search_limit_cap
    decision = await authorize(db, subject, Action.SEARCH, detail={"query": q}, defer_allowed=True)
    results = await search(db, subject, q, min(limit, cap))
    # The audit row lists exactly what was returned, committed before the response (R28).
    await record(
        db,
        subject,
        decision,
        returned_version_ids=returned_ids(results),
        detail={"query": q, "limit": min(limit, cap), "count": len(results)},
    )
    return {"results": results}
