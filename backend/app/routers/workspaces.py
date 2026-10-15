import logging
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app import security
from app.audit import authorize, not_found, record
from app.auth import Member, SignedIn, load_subject
from app.db import DB, CommitThenRaise
from app.models import Invite, Membership, User, Workspace
from app.policy import Action, Decision, decide
from app.services import workspaces as svc

router = APIRouter(tags=["workspaces"])
log = logging.getLogger("lattice.workspaces")


class NewWorkspace(BaseModel):
    name: str


class NewInvite(BaseModel):
    role: Literal["admin", "member"]


class RoleChange(BaseModel):
    role: Literal["admin", "member"]


class Transfer(BaseModel):
    user_id: UUID


class AcceptInvite(BaseModel):
    token: str


@router.post("/api/workspaces", status_code=201)
async def create_workspace(body: NewWorkspace, user: SignedIn, db: DB) -> dict:
    workspace = Workspace(name=svc.validate_name(body.name))
    db.add(workspace)
    await db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=user.user_id, role="owner"))
    await db.flush()
    subject = await load_subject(db, user.user_id, workspace.id)
    await authorize(db, subject, Action.CREATE_WORKSPACE)
    return {"id": str(workspace.id), "name": workspace.name}


# --- Members ------------------------------------------------------------------------------


@router.get("/api/workspaces/{workspace_id}/members")
async def list_members(subject: Member, db: DB) -> dict:
    await authorize(db, subject, Action.VIEW_ROSTER)
    rows = await db.execute(
        select(User.id, User.display_name, User.email, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.workspace_id == subject.workspace_id)
        .order_by(User.display_name)
    )
    return {
        "members": [
            {"user_id": str(uid), "name": name, "email": email, "role": role}
            for uid, name, email, role in rows
        ]
    }


@router.patch("/api/workspaces/{workspace_id}/members/{user_id}")
async def change_role(user_id: UUID, body: RoleChange, subject: Member, db: DB) -> dict:
    target = await svc.membership(db, subject.workspace_id, user_id)
    await authorize(
        db,
        subject,
        Action.CHANGE_ROLE,
        target_role=target.role if target else None,  # the policy refuses the owner
        detail={"target_user_id": str(user_id)},
    )
    # Demotion changes only the role; tokens and groups stay (Assumptions, AE8).
    await svc.set_role(db, subject.workspace_id, user_id, target.role, body.role)
    return {"user_id": str(user_id), "role": body.role}


@router.delete("/api/workspaces/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(user_id: UUID, subject: Member, db: DB) -> None:
    target = await svc.membership(db, subject.workspace_id, user_id)
    await authorize(
        db,
        subject,
        Action.REMOVE_MEMBER,
        target_role=target.role if target else None,
        detail={"target_user_id": str(user_id)},
    )
    await svc.remove_member(db, subject.workspace_id, user_id, target.role)


@router.post("/api/workspaces/{workspace_id}/transfer")
async def transfer_ownership(body: Transfer, subject: Member, db: DB) -> dict:
    target = await svc.membership(db, subject.workspace_id, body.user_id)
    await authorize(
        db,
        subject,
        Action.TRANSFER_OWNERSHIP,
        target_role=target.role if target else None,
        detail={"target_user_id": str(body.user_id)},
    )
    await svc.transfer_ownership(
        db, subject.workspace_id, subject.user_id, body.user_id, target.role
    )
    return {"owner_id": str(body.user_id)}


# --- Invites ------------------------------------------------------------------------------


@router.post("/api/workspaces/{workspace_id}/invites", status_code=201)
async def create_invite(body: NewInvite, subject: Member, request: Request, db: DB) -> dict:
    await authorize(db, subject, Action.INVITE, target_role=body.role)
    settings = request.app.state.settings
    issued = security.issue("inv", settings.secret_pepper)
    invite = Invite(
        workspace_id=subject.workspace_id,
        role=body.role,
        inviter_id=subject.user_id,
        prefix=issued.prefix,
        verifier=issued.verifier,
        expires_at=datetime.now(UTC) + settings.invite_lifetime(body.role),
    )
    db.add(invite)
    await db.flush()
    return {
        "id": str(invite.id),
        "role": invite.role,
        "expires_at": invite.expires_at.isoformat(),
        # The secret rides in the fragment, which browsers never send to servers (KTD7).
        "invite_url": f"{settings.public_origin}/invite#{issued.value}",
    }


@router.get("/api/workspaces/{workspace_id}/invites")
async def list_invites(subject: Member, db: DB) -> dict:
    await authorize(db, subject, Action.VIEW_ROSTER)
    invites = await db.scalars(
        select(Invite)
        .where(
            Invite.workspace_id == subject.workspace_id,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.expires_at > datetime.now(UTC),
        )
        .order_by(Invite.created_at.desc())
    )
    return {
        "invites": [
            {
                "id": str(i.id),
                "role": i.role,
                "prefix": i.prefix,
                "inviter_id": str(i.inviter_id),
                "expires_at": i.expires_at.isoformat(),
            }
            for i in invites
        ]
    }


@router.delete("/api/workspaces/{workspace_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(invite_id: UUID, subject: Member, db: DB) -> None:
    await authorize(db, subject, Action.REVOKE_INVITE, detail={"invite_id": str(invite_id)})
    result = await db.execute(
        update(Invite)
        .where(
            Invite.id == invite_id,
            Invite.workspace_id == subject.workspace_id,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    if result.rowcount == 0:
        raise not_found()


@router.post("/api/invites/accept")
async def accept_invite(body: AcceptInvite, request: Request, user: SignedIn, db: DB) -> dict:
    pepper = request.app.state.settings.secret_pepper
    prefix = security.parse_prefix(body.token, "inv")
    invite = await db.scalar(select(Invite).where(Invite.prefix == prefix)) if prefix else None
    if invite is None or not security.matches(body.token, invite.verifier, pepper):
        log.info("invite refused: unknown token user=%s", user.user_id)
        raise not_found()

    subject = await load_subject(db, user.user_id, invite.workspace_id)
    reason = await _invite_problem(db, invite, subject)
    if reason:
        refusal = Decision(
            False, f"accept_invite.{reason}", {"action": "accept_invite", "role": subject.role}
        )
        await record(
            db, subject, refusal, detail={"invite_id": str(invite.id)}, holder_of_secret=True
        )
        raise CommitThenRaise(not_found())

    # Conditional update: of two concurrent acceptances, exactly one claims the invite (R6).
    claimed = await db.execute(
        update(Invite)
        .where(
            Invite.id == invite.id,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.expires_at > datetime.now(UTC),
        )
        .values(accepted_at=datetime.now(UTC), accepted_by=user.user_id)
    )
    if claimed.rowcount == 0:
        raise not_found()
    try:
        async with db.begin_nested():
            db.add(
                Membership(workspace_id=invite.workspace_id, user_id=user.user_id, role=invite.role)
            )
    except IntegrityError:
        raise not_found() from None
    member = await load_subject(db, user.user_id, invite.workspace_id)
    await authorize(db, member, Action.ACCEPT_INVITE, detail={"invite_id": str(invite.id)})
    return {"workspace_id": str(invite.workspace_id), "role": invite.role}


async def _invite_problem(db, invite: Invite, subject) -> str | None:
    now = datetime.now(UTC)
    if invite.accepted_at is not None:
        return "used"
    if invite.revoked_at is not None:
        return "revoked"
    if invite.expires_at <= now:
        return "expired"
    if subject.role is not None:
        return "already_member"  # roles change only through R7 and R8
    # The inviter must still hold the authority to issue this role (R6).
    inviter = await load_subject(db, invite.inviter_id, invite.workspace_id)
    if not decide(inviter, Action.INVITE, target_role=invite.role).allowed:
        return "inviter_lost_authority"
    return None
