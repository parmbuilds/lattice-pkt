"""Browser identity (KTD5): session cookies, CSRF, request guards, and loading the subject."""

import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import security
from app.config import Settings
from app.db import DB
from app.models import GroupMember, Membership, User
from app.models import Session as SessionRow
from app.policy import Channel, Subject

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SECURITY_HEADERS = [
    (
        b"content-security-policy",
        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        b"connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


def cookie_name(settings: Settings) -> str:
    # The __Host- prefix requires Secure, so local HTTP development uses a plain name.
    return "lattice_session" if settings.insecure_dev_cookies else "__Host-lattice_session"


class GuardMiddleware:
    """Hardening headers everywhere (KTD13); on /api, refuse bearer credentials (KTD5) and
    unsafe requests whose Origin is missing or foreign."""

    def __init__(self, app, settings: Settings):
        self.app = app
        self.origin = settings.public_origin

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + SECURITY_HEADERS
            await send(message)

        if scope["path"].startswith("/api"):
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if headers.get("authorization", "").lower().startswith("bearer"):
                return await reject(send_with_headers, 401, "Bearer tokens are for /mcp only.")
            if scope["method"] not in SAFE_METHODS and headers.get("origin") != self.origin:
                return await reject(send_with_headers, 403, "Cross-origin request refused.")
        await self.app(scope, receive, send_with_headers)


async def reject(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


@dataclass(frozen=True)
class CurrentUser:
    user_id: UUID
    session_id: UUID
    csrf_token: str
    email: str
    display_name: str


UNAUTHENTICATED = HTTPException(status_code=401, detail="Sign in required.")


async def current_user(request: Request, db: DB) -> CurrentUser:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(cookie_name(settings))
    prefix = security.parse_prefix(token or "", "ses")
    if not prefix:
        raise UNAUTHENTICATED
    row = await db.scalar(select(SessionRow).where(SessionRow.prefix == prefix))
    now = datetime.now(UTC)
    if (
        row is None
        or not security.matches(token, row.verifier, settings.secret_pepper)
        or row.revoked_at is not None
        or row.expires_at <= now
        or row.last_seen_at + settings.session_idle <= now
    ):
        raise UNAUTHENTICATED
    await db.execute(update(SessionRow).where(SessionRow.id == row.id).values(last_seen_at=now))
    user = await db.get(User, row.user_id)
    return CurrentUser(user.id, row.id, row.csrf_token, user.email, user.display_name)


async def require_csrf(request: Request, user: Annotated[CurrentUser, Depends(current_user)]):
    """Unsafe methods must echo the session's CSRF token (synchronizer pattern)."""
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent.encode(), user.csrf_token.encode()):
            raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")
    return user


SignedIn = Annotated[CurrentUser, Depends(require_csrf)]


async def load_subject(
    db: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    channel: Channel = "ui",
    token_id: UUID | None = None,
) -> Subject:
    """The live role and groups of a user in a workspace (R25); role None means non-member."""
    role = await db.scalar(
        select(Membership.role).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user_id
        )
    )
    groups = (
        await db.scalars(
            select(GroupMember.group_id).where(
                GroupMember.workspace_id == workspace_id, GroupMember.user_id == user_id
            )
        )
        if role
        else []
    )
    return Subject(
        user_id=user_id,
        workspace_id=workspace_id,
        role=role,
        group_ids=frozenset(groups),
        channel=channel,
        token_id=token_id,
    )


async def workspace_subject(workspace_id: UUID, user: SignedIn, db: DB) -> Subject:
    """The signed-in user's live role and groups in the workspace named by the path."""
    return await load_subject(db, user.user_id, workspace_id)


Member = Annotated[Subject, Depends(workspace_subject)]
