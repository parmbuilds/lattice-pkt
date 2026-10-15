from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from app.auth import GuardMiddleware
from app.config import DEV_PEPPER, Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.mcp_server import build_mcp, transport_security
from app.routers import audit as audit_routes
from app.routers import auth as auth_routes
from app.routers import documents as document_routes
from app.routers import groups as group_routes
from app.routers import search as search_routes
from app.routers import tokens as token_routes
from app.routers import workspaces as workspace_routes


class SpaFiles(StaticFiles):
    """Serves the built UI; unknown paths get index.html so client-side routes work."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.secret_pepper == DEV_PEPPER and not settings.insecure_dev_cookies:
        # The default is public; outside local development it would make every verifier forgeable.
        raise RuntimeError("Set LATTICE_SECRET_PEPPER to a private value.")
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    mcp = build_mcp(settings, sessionmaker)
    mcp_asgi = mcp.streamable_http_app(transport_security=transport_security(settings))
    static_dir = Path(settings.static_dir) if settings.static_dir else None
    spa = SpaFiles(directory=static_dir, html=True) if static_dir and static_dir.is_dir() else None

    async def root(scope, receive, send):
        # /mcp goes to the MCP server; everything else outside /api is the UI (KTD12).
        path = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/") or spa is None:
            return await mcp_asgi(scope, receive, send)
        return await spa(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # A mounted sub-app's lifespan does not run, so the host runs the MCP session manager.
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Lattice", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.add_middleware(GuardMiddleware, settings=settings)
    for module in (
        auth_routes,
        audit_routes,
        workspace_routes,
        group_routes,
        document_routes,
        search_routes,
        token_routes,
    ):
        app.include_router(module.router)

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, str]:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    app.mount("/", root)  # last: API routes take precedence
    return app
