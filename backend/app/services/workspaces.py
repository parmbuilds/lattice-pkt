"""Workspace membership operations. Authorization happens in the routers via app.audit."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessToken, Membership


def validate_name(value: str) -> str:
    """Workspace and group names: 1 to 100 characters after trimming."""
    value = value.strip()
    if not 1 <= len(value) <= 100:
        raise HTTPException(400, "Names must be 1 to 100 characters.")
    return value


ROLE_CHANGED = HTTPException(409, "This member's role changed. Reload and retry.")


async def membership(db: AsyncSession, workspace_id: UUID, user_id: UUID) -> Membership | None:
    return await db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user_id
        )
    )


async def remove_member(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, authorized_role: str
) -> None:
    """Group memberships and direct grants go with the membership (FK cascade); tokens are
    revoked, not deleted, so audit rows keep their reference. Authorship stays. Guarded on
    the authorized role, like set_role: a concurrent promotion makes this a conflict."""
    result = await db.execute(
        delete(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
            Membership.role == authorized_role,
        )
    )
    if result.rowcount != 1:
        raise ROLE_CHANGED
    await db.execute(
        update(AccessToken)
        .where(
            AccessToken.workspace_id == workspace_id,
            AccessToken.user_id == user_id,
            AccessToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


async def set_role(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, authorized_role: str, role: str
) -> None:
    """Guarded on the role that was authorized (KTD4): a concurrent transfer or role change
    makes this a conflict instead of overwriting it, so a workspace never loses its owner."""
    result = await db.execute(
        update(Membership)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
            Membership.role == authorized_role,
        )
        .values(role=role)
    )
    if result.rowcount != 1:
        raise ROLE_CHANGED


async def transfer_ownership(
    db: AsyncSession, workspace_id: UUID, owner_id: UUID, new_owner_id: UUID, target_role: str
) -> None:
    # Demote first: the one-owner index allows zero owners mid-transaction, never two.
    await set_role(db, workspace_id, owner_id, "owner", "admin")
    await db.flush()
    await set_role(db, workspace_id, new_owner_id, target_role, "owner")
