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
