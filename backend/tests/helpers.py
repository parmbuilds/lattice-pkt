import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.config import Settings
from app.main import create_app

PASSWORD = "correct horse battery"


@asynccontextmanager
async def api_client(settings: Settings, app=None) -> AsyncIterator[httpx.AsyncClient]:
    """A same-origin browser-like client: sends our Origin on every request."""
    app = app or create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=settings.public_origin,
        headers={"Origin": settings.public_origin},
    ) as client:
        yield client
    await app.state.engine.dispose()


async def sign_up_and_in(client: httpx.AsyncClient, email: str | None = None) -> str:
    """Create an account, sign in, and return the CSRF token."""
    email = email or f"{uuid.uuid4()}@example.test"
    await client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD, "name": email[:8]}
    )
    response = await client.post("/api/auth/signin", json={"email": email, "password": PASSWORD})
    response.raise_for_status()
    return response.json()["csrf_token"]


async def user_id(client: httpx.AsyncClient) -> str:
    return (await client.get("/api/auth/me")).json()["user"]["id"]


def seed_workspace(pg: dict[str, str], members: dict[str, str]) -> str:
    """Insert a workspace and memberships directly (user_id -> role); returns its id."""
    import psycopg

    from tests.conftest import plain_dsn

    with psycopg.connect(plain_dsn(pg["app"])) as conn:
        workspace_id = conn.execute(
            "INSERT INTO workspaces (name) VALUES ('Seeded') RETURNING id"
        ).fetchone()[0]
        for uid, role in members.items():
            conn.execute(
                "INSERT INTO memberships (workspace_id, user_id, role) VALUES (%s, %s, %s)",
                (workspace_id, uid, role),
            )
    return str(workspace_id)


@asynccontextmanager
async def signed_in(settings: Settings, app=None) -> AsyncIterator[httpx.AsyncClient]:
    """A signed-in client with its CSRF header set; `client.user_id` holds the user's id."""
    async with api_client(settings, app) as client:
        client.headers["X-CSRF-Token"] = await sign_up_and_in(client)
        client.user_id = await user_id(client)
        yield client


async def create_workspace(client: httpx.AsyncClient, name: str = "Acme") -> str:
    response = await client.post("/api/workspaces", json={"name": name})
    response.raise_for_status()
    return response.json()["id"]


async def invite(client: httpx.AsyncClient, workspace_id: str, role: str = "member") -> str:
    """Return the invite token taken from the link's fragment."""
    response = await client.post(f"/api/workspaces/{workspace_id}/invites", json={"role": role})
    response.raise_for_status()
    return response.json()["invite_url"].split("#", 1)[1]


async def join(
    owner: httpx.AsyncClient, workspace_id: str, newcomer: httpx.AsyncClient, role: str = "member"
) -> None:
    token = await invite(owner, workspace_id, role)
    response = await newcomer.post("/api/invites/accept", json={"token": token})
    response.raise_for_status()


async def add_document(
    client: httpx.AsyncClient,
    workspace_id: str,
    title="Runbook",
    body="Restart the checkout service.",
) -> dict:
    response = await client.post(
        f"/api/workspaces/{workspace_id}/documents", json={"title": title, "body": body}
    )
    response.raise_for_status()
    return response.json()


async def approve(client: httpx.AsyncClient, workspace_id: str, document: dict) -> None:
    response = await client.post(
        f"/api/workspaces/{workspace_id}/documents/{document['id']}/approve",
        json={"version_id": document["pending_version_id"]},
    )
    response.raise_for_status()


async def derive_all(app) -> list[uuid.UUID]:
    """Run worker ticks until nothing is due; returns the versions derived, in order."""
    from app.services.derive import tick

    derived = []
    while version := await tick(app.state.sessionmaker, app.state.embedder, app.state.settings):
        derived.append(version)
    return derived


async def retrievable_ids(pg, workspace_id: str, uid: str) -> set[str]:
    """What the policy's retrieve predicate returns for a user right now (live role and groups)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.auth import load_subject
    from app.db import make_sessionmaker
    from app.policy import Action, readable_versions

    engine = create_async_engine(pg["app"])
    try:
        async with make_sessionmaker(engine)() as db:
            subject = await load_subject(db, uuid.UUID(uid), uuid.UUID(workspace_id))
            return {str(v) for v in await db.scalars(readable_versions(subject, Action.RETRIEVE))}
    finally:
        await engine.dispose()


def seed_turn(
    pg: dict[str, str], workspace_id: str, user_id: str, cited: list[tuple[str, str]]
) -> tuple[str, str]:
    """A conversation with one turn citing (document_id, version_id) spans, inserted directly."""
    import psycopg

    from tests.conftest import plain_dsn

    with psycopg.connect(plain_dsn(pg["app"])) as conn:
        conversation = conn.execute(
            "INSERT INTO conversations (workspace_id, user_id, title) VALUES (%s, %s, 'q') "
            "RETURNING id",
            (workspace_id, user_id),
        ).fetchone()[0]
        turn = conn.execute(
            "INSERT INTO turns (workspace_id, conversation_id, ordinal, question, sentences, "
            "outcome, provider_id, model) VALUES (%s, %s, 1, 'q', '[]', 'answered', 'fake', 'm') "
            "RETURNING id",
            (workspace_id, conversation),
        ).fetchone()[0]
        for ordinal, (document_id, version_id) in enumerate(cited, start=1):
            conn.execute(
                "INSERT INTO turn_passages (turn_id, ordinal, workspace_id, document_id, "
                "version_id, start, \"end\", sha256) VALUES (%s, %s, %s, %s, %s, 0, 1, 'x')",
                (turn, ordinal, workspace_id, document_id, version_id),
            )
    return str(conversation), str(turn)


def seed_summary(pg: dict[str, str], workspace_id: str, conversation_id: str, sources):
    """Insert a summary drawing on (document_id, version_id) sources; returns its id."""
    import psycopg

    from tests.conftest import plain_dsn

    with psycopg.connect(plain_dsn(pg["app"])) as conn:
        summary = conn.execute(
            "INSERT INTO conversation_summaries (workspace_id, conversation_id, text, "
            "covers_through, model) VALUES (%s, %s, 's', 1, 'm') RETURNING id",
            (workspace_id, conversation_id),
        ).fetchone()[0]
        for document_id, version_id in sources:
            conn.execute(
                "INSERT INTO summary_sources (summary_id, workspace_id, document_id, version_id) "
                "VALUES (%s, %s, %s, %s)",
                (summary, workspace_id, document_id, version_id),
            )
    return str(summary)


def seed_turn_sources(pg: dict[str, str], workspace_id: str, turn_id: str, sources) -> None:
    """Record (document_id, version_id) summary sources a turn was built on, inserted directly."""
    import psycopg

    from tests.conftest import plain_dsn

    with psycopg.connect(plain_dsn(pg["app"])) as conn:
        for document_id, version_id in sources:
            conn.execute(
                "INSERT INTO turn_sources (turn_id, workspace_id, document_id, version_id) "
                "VALUES (%s, %s, %s, %s)",
                (turn_id, workspace_id, document_id, version_id),
            )
