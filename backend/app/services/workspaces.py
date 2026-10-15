"""Workspace membership operations. Authorization happens in the routers via app.audit."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessToken, Membership


async def membership(db: AsyncSession, workspace_id: UUID, user_id: UUID) -> Membership | None:
    return await db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user_id
        )
    )


async def remove_member(db: AsyncSession, workspace_id: UUID, user_id: UUID) -> None:
    """Group memberships and direct grants go with the membership (FK cascade); tokens are
    revoked, not deleted, so audit rows keep their reference. Authorship stays."""
    await db.execute(
        delete(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user_id
        )
    )
    await db.execute(
        update(AccessToken)
        .where(
            AccessToken.workspace_id == workspace_id,
            AccessToken.user_id == user_id,
            AccessToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


async def set_role(db: AsyncSession, workspace_id: UUID, user_id: UUID, role: str) -> None:
    await db.execute(
        update(Membership)
        .where(Membership.workspace_id == workspace_id, Membership.user_id == user_id)
        .values(role=role)
    )


async def transfer_ownership(
    db: AsyncSession, workspace_id: UUID, owner_id: UUID, new_owner_id: UUID
) -> None:
    # Demote first: the one-owner index allows zero owners mid-transaction, never two.
    await set_role(db, workspace_id, owner_id, "admin")
    await db.flush()
    await set_role(db, workspace_id, new_owner_id, "owner")
