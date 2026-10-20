"""Conversation retention (R20, KTD11): reads treat an expired conversation as absent, and a
sweep next to the derivation worker deletes it."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai import turns
from app.auth import load_subject
from app.config import Settings
from app.models import Conversation
from app.policy import Action, Subject, decide
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


async def abandon_stale(sessionmaker: async_sessionmaker, settings: Settings) -> None:
    """Record turns whose mark outlived the turn timeout as abandoned and clear their marks
    (KTD5 step 5); a conversation a turn still has locked waits for the next sweep."""
    async with sessionmaker.begin() as db:
        stale = await db.execute(
            select(
                Conversation.id,
                Conversation.workspace_id,
                Conversation.user_id,
                Conversation.in_flight_turn,
            )
            .where(Conversation.in_flight_at < func.now() - turns.turn_timeout(settings))
            .order_by(Conversation.id)
            .with_for_update(skip_locked=True)
        )
        for conversation_id, workspace_id, user_id, number in stale.all():
            subject = await load_subject(db, user_id, workspace_id)
            decision = decide(subject, Action.ASK)
            await turns.finish(db, subject, decision, conversation_id, number, "abandoned")


async def sweep(sessionmaker: async_sessionmaker, settings: Settings) -> int:
    """Delete every workspace's expired conversations, one transaction per workspace, after
    abandoning stale turns."""
    await abandon_stale(sessionmaker, settings)
    async with sessionmaker() as db:
        workspaces = (await db.scalars(select(Conversation.workspace_id).distinct())).all()
    deleted = 0
    for workspace_id in workspaces:
        async with sessionmaker.begin() as db:
            expired = await cutoff(db, workspace_id)
            deleted += await ai_settings.delete_expired(db, workspace_id, expired)
    return deleted


async def run_sweep(sessionmaker: async_sessionmaker, settings: Settings) -> None:
    """The retention loop the app lifespan runs beside the derivation worker."""
    while True:
        try:
            await sweep(sessionmaker, settings)
        except Exception:
            log.exception("retention sweep failed")
        await asyncio.sleep(SWEEP_SECONDS)
