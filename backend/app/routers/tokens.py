from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update

from app import security
from app.audit import authorize, not_found
from app.auth import Member
from app.db import DB
from app.models import AccessToken, User
from app.policy import Action

router = APIRouter(prefix="/api/workspaces/{workspace_id}/tokens", tags=["tokens"])


class NewToken(BaseModel):
    name: str
    password: str


@router.post("", status_code=201)
async def create_token(body: NewToken, subject: Member, request: Request, db: DB) -> dict:
    """Shown once; bound to one workspace; requires re-entering the password (KTD5, KTD7)."""
    await authorize(db, subject, Action.MANAGE_OWN_TOKENS, detail={"op": "create"})
    user = await db.get(User, subject.user_id)
    if not security.check_password(body.password, user.password_hash):
        raise HTTPException(403, "Re-enter your password to create a token.")
    name = body.name.strip()
    if not 1 <= len(name) <= 100:
        raise HTTPException(400, "Token names need 1-100 characters.")
    settings = request.app.state.settings
    issued = security.issue("pat", settings.secret_pepper)
    token = AccessToken(
        workspace_id=subject.workspace_id,
        user_id=subject.user_id,
        name=name,
        prefix=issued.prefix,
        verifier=issued.verifier,
        expires_at=datetime.now(UTC) + timedelta(days=settings.token_days),
    )
    db.add(token)
    await db.flush()
    await db.refresh(token)
    return _view(token) | {"token": issued.value}


@router.get("")
async def list_tokens(subject: Member, db: DB) -> dict:
    await authorize(db, subject, Action.MANAGE_OWN_TOKENS, detail={"op": "list"})
    tokens = await db.scalars(
        select(AccessToken)
        .where(
            AccessToken.workspace_id == subject.workspace_id,
            AccessToken.user_id == subject.user_id,
        )
        .order_by(AccessToken.created_at.desc())
    )
    return {"tokens": [_view(t) for t in tokens]}


@router.delete("/{token_id}", status_code=204)
async def revoke_token(token_id: UUID, subject: Member, db: DB) -> None:
    await authorize(
        db, subject, Action.MANAGE_OWN_TOKENS, detail={"op": "revoke", "token_id": str(token_id)}
    )
    result = await db.execute(
        update(AccessToken)
        .where(
            AccessToken.id == token_id,
            AccessToken.workspace_id == subject.workspace_id,
            AccessToken.user_id == subject.user_id,
            AccessToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    if result.rowcount == 0:
        raise not_found()


def _view(t: AccessToken) -> dict:
    return {
        "id": str(t.id),
        "name": t.name,
        "prefix": t.prefix,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "expires_at": t.expires_at.isoformat(),
        "revoked": t.revoked_at is not None,
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
    }
