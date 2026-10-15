from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from mcp.server import MCPServer
from sqlalchemy import text

from app.auth import GuardMiddleware
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.mcp_server import transport_security
from app.routers import audit as audit_routes
from app.routers import auth as auth_routes
from app.routers import groups as group_routes
from app.routers import workspaces as workspace_routes


def create_app(settings: Settings | None = None, mcp: MCPServer | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    mcp_asgi = (
        mcp.streamable_http_app(transport_security=transport_security(settings)) if mcp else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            if mcp:
                # A mounted sub-app's lifespan does not run, so the host runs the MCP session
                # manager itself.
                async with mcp.session_manager.run():
                    yield
            else:
                yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Lattice", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.add_middleware(GuardMiddleware, settings=settings)
    app.include_router(auth_routes.router)
    app.include_router(audit_routes.router)
    app.include_router(workspace_routes.router)
    app.include_router(group_routes.router)

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, str]:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    if mcp_asgi:
        # Mounted last: "/" matches everything, and the MCP endpoint stays at /mcp.
        app.mount("/", mcp_asgi)
    return app
