"""Conversation retention (R20, KTD11): reads treat an expired conversation as absent, and a
sweep next to the derivation worker deletes it."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Conversation
from app.policy import Subject
from app.services import ai_settings

log = logging.getLogger("lattice.retention")

# Reads already hide expired conversations, so the sweep's pace only bounds storage.
SWEEP_SECONDS = 3600


async def cutoff(db: AsyncSession, workspace_id: UUID) -> datetime:
    """Conversations whose last turn is older than this have expired."""
    row = await ai_settings.current(db, workspace_id)
    days = row.retention_days if row else ai_settings.DEFAULT_RETENTION_DAYS
    return datetime.now(UTC) - timedelta(days=days)


async def own_unexpired(db: AsyncSession, subject: Subject) -> tuple:
    """Where-clauses for the subject's own conversations that have not expired (R19, R20)."""
    return (
        Conversation.workspace_id == subject.workspace_id,
        Conversation.user_id == subject.user_id,
        Conversation.last_turn_at >= await cutoff(db, subject.workspace_id),
    )


async def sweep(sessionmaker: async_sessionmaker) -> int:
    """Delete every workspace's expired conversations, one transaction per workspace."""
    async with sessionmaker() as db:
        workspaces = (await db.scalars(select(Conversation.workspace_id).distinct())).all()
    deleted = 0
    for workspace_id in workspaces:
        async with sessionmaker.begin() as db:
            expired = await cutoff(db, workspace_id)
            deleted += await ai_settings.delete_expired(db, workspace_id, expired)
    return deleted


async def run_sweep(sessionmaker: async_sessionmaker) -> None:
    """The retention loop the app lifespan runs beside the derivation worker."""
    while True:
        try:
            await sweep(sessionmaker)
        except Exception:
            log.exception("retention sweep failed")
        await asyncio.sleep(SWEEP_SECONDS)
