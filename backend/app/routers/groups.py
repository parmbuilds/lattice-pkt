from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.audit import authorize, not_found, record
from app.auth import Member
from app.db import DB
from app.models import DocumentGrant, Group, GroupMember, User
from app.policy import Action, decide
from app.routers.workspaces import _name

router = APIRouter(prefix="/api/workspaces/{workspace_id}/groups", tags=["groups"])


class GroupName(BaseModel):
    name: str


@router.get("")
async def list_groups(subject: Member, db: DB) -> dict:
    """Managers see every group and its members; members see only their own groups' names."""
    if subject.role is None:
        raise not_found()
    roster = decide(subject, Action.VIEW_ROSTER)
    query = select(Group).where(Group.workspace_id == subject.workspace_id).order_by(Group.name)
    if not roster.allowed:
        groups = await db.scalars(query.where(Group.id.in_(list(subject.group_ids))))
        return {"groups": [{"id": str(g.id), "name": g.name} for g in groups]}
    await record(db, subject, roster)
    groups = list(await db.scalars(query))
    members = await db.execute(
        select(GroupMember.group_id, User.id, User.display_name)
        .join(User, User.id == GroupMember.user_id)
        .where(GroupMember.workspace_id == subject.workspace_id)
    )
    by_group: dict[UUID, list[dict]] = {}
    for group_id, uid, name in members:
        by_group.setdefault(group_id, []).append({"user_id": str(uid), "name": name})
    return {
        "groups": [
            {"id": str(g.id), "name": g.name, "members": by_group.get(g.id, [])} for g in groups
        ]
    }


@router.post("", status_code=201)
async def create_group(body: GroupName, subject: Member, db: DB) -> dict:
    await authorize(db, subject, Action.MANAGE_GROUPS, detail={"op": "create"})
    group = Group(workspace_id=subject.workspace_id, name=_name(body.name))
    try:
        async with db.begin_nested():
            db.add(group)
    except IntegrityError:
        raise HTTPException(409, "A group with that name already exists.") from None
    return {"id": str(group.id), "name": group.name}


@router.patch("/{group_id}")
async def rename_group(group_id: UUID, body: GroupName, subject: Member, db: DB) -> dict:
    await authorize(
        db, subject, Action.MANAGE_GROUPS, detail={"op": "rename", "group_id": str(group_id)}
    )
    group = await _group(db, subject, group_id)
    try:
        async with db.begin_nested():
            group.name = _name(body.name)
    except IntegrityError:
        raise HTTPException(409, "A group with that name already exists.") from None
    return {"id": str(group.id), "name": group.name}


@router.delete("/{group_id}")
async def delete_group(group_id: UUID, subject: Member, db: DB) -> dict:
    await authorize(
        db, subject, Action.MANAGE_GROUPS, detail={"op": "delete", "group_id": str(group_id)}
    )
    group = await _group(db, subject, group_id)
    grants = await db.scalar(
        select(func.count()).select_from(DocumentGrant).where(DocumentGrant.group_id == group.id)
    )
    await db.delete(group)  # grants and memberships cascade (R10)
    return {"grants_removed": grants}


@router.put("/{group_id}/members/{user_id}", status_code=204)
async def add_member(group_id: UUID, user_id: UUID, subject: Member, db: DB) -> None:
    await authorize(
        db,
        subject,
        Action.MANAGE_GROUPS,
        detail={"op": "add_member", "group_id": str(group_id), "user_id": str(user_id)},
    )
    try:
        async with db.begin_nested():
            await db.execute(
                insert(GroupMember)
                .values(workspace_id=subject.workspace_id, group_id=group_id, user_id=user_id)
                .on_conflict_do_nothing()
            )
    except IntegrityError:  # the group or the person is not in this workspace
        raise not_found() from None


@router.delete("/{group_id}/members/{user_id}", status_code=204)
async def remove_member(group_id: UUID, user_id: UUID, subject: Member, db: DB) -> None:
    await authorize(
        db,
        subject,
        Action.MANAGE_GROUPS,
        detail={"op": "remove_member", "group_id": str(group_id), "user_id": str(user_id)},
    )
    await db.execute(
        delete(GroupMember).where(
            GroupMember.workspace_id == subject.workspace_id,
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
    )


async def _group(db, subject, group_id: UUID) -> Group:
    group = await db.scalar(
        select(Group).where(Group.id == group_id, Group.workspace_id == subject.workspace_id)
    )
    if group is None:
        raise not_found()
    return group
