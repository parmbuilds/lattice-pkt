from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.audit import authorize
from app.auth import Member
from app.db import DB
from app.models import AuditEvent
from app.policy import Action

router = APIRouter(prefix="/api/workspaces/{workspace_id}/audit", tags=["audit"])


@router.get("")
async def list_events(
    subject: Member,
    db: DB,
    user_id: UUID | None = None,
    action: str | None = None,
    outcome: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """Admins and the owner read and filter the workspace trail (R30)."""
    await authorize(db, subject, Action.VIEW_AUDIT)
    query = select(AuditEvent).where(AuditEvent.workspace_id == subject.workspace_id)
    if user_id:
        query = query.where(AuditEvent.actor_user_id == user_id)
    if action:
        query = query.where(AuditEvent.action == action)
    if outcome:
        query = query.where(AuditEvent.outcome == outcome)
    if since:
        query = query.where(AuditEvent.occurred_at >= since)
    events = await db.scalars(query.order_by(AuditEvent.id.desc()).limit(limit))
    return {"events": [_event(e) for e in events]}


def _event(e: AuditEvent) -> dict:
    return {
        "id": e.id,
        "occurred_at": e.occurred_at.isoformat(),
        "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None,
        "channel": e.channel,
        "token_id": str(e.token_id) if e.token_id else None,
        "action": e.action,
        "outcome": e.outcome,
        "rule_id": e.rule_id,
        "inputs": e.inputs,
        "document_id": str(e.document_id) if e.document_id else None,
        "version_id": str(e.version_id) if e.version_id else None,
        "returned_version_ids": [str(v) for v in e.returned_version_ids or []],
        "detail": e.detail,
    }
