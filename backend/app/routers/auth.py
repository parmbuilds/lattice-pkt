import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app import security
from app.auth import SignedIn, cookie_name, current_user
from app.db import DB
from app.models import Membership, User, Workspace
from app.models import Session as SessionRow

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger("lattice.auth")

# Sign-up and sign-in are global to the user, so they go to the application log, not a
# workspace audit trail (Assumptions).
SIGNUP_REJECTED = HTTPException(400, "Could not create an account with these details.")
SIGNIN_REJECTED = HTTPException(401, "Invalid email or password.")


class SignUp(BaseModel):
    email: str
    password: str
    name: str


class SignIn(BaseModel):
    email: str
    password: str


@router.post("/signup", status_code=201)
async def sign_up(body: SignUp, db: DB) -> dict:
    email = body.email.strip()
    if (
        "@" not in email
        or len(email) > 254
        or not 8 <= len(body.password) <= 256
        or not 1 <= len(body.name.strip()) <= 100
    ):
        raise SIGNUP_REJECTED
    try:
        async with db.begin_nested():
            user = User(
                email=email,
                display_name=body.name.strip(),
                password_hash=await security.hash_password(body.password),
            )
            db.add(user)
    except IntegrityError:
        # Same response as any other rejected sign-up.
        raise SIGNUP_REJECTED from None
    log.info("sign-up user=%s", user.id)
    return {"id": str(user.id)}


@router.post("/signin")
async def sign_in(body: SignIn, request: Request, response: Response, db: DB) -> dict:
    settings = request.app.state.settings
    user = await db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if not await security.check_password(body.password, user.password_hash if user else None):
        log.info("sign-in refused")
        raise SIGNIN_REJECTED
    issued = security.issue("ses", settings.secret_pepper)
    now = datetime.now(UTC)
    session = SessionRow(
        user_id=user.id,
        prefix=issued.prefix,
        verifier=issued.verifier,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=now + settings.session_absolute,
    )
    db.add(session)
    response.set_cookie(
        cookie_name(settings),
        issued.value,
        max_age=int(settings.session_absolute.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=not settings.insecure_dev_cookies,
        path="/",
    )
    log.info("sign-in user=%s", user.id)
    return {"csrf_token": session.csrf_token}


@router.post("/signout", status_code=204)
async def sign_out(user: SignedIn, request: Request, response: Response, db: DB) -> None:
    await db.execute(
        update(SessionRow)
        .where(SessionRow.id == user.session_id)
        .values(revoked_at=datetime.now(UTC))
    )
    response.delete_cookie(cookie_name(request.app.state.settings), path="/")


@router.get("/me")
async def me(request: Request, db: DB) -> dict:
    user = await current_user(request, db)
    rows = await db.execute(
        select(Membership.workspace_id, Workspace.name, Membership.role)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(Membership.user_id == user.user_id)
        .order_by(Workspace.name)
    )
    return {
        "user": {"id": str(user.user_id), "email": user.email, "name": user.display_name},
        "csrf_token": user.csrf_token,
        "memberships": [
            {"workspace_id": str(ws), "workspace_name": name, "role": role}
            for ws, name, role in rows
        ],
    }
