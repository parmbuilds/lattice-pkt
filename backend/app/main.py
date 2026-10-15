import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from app.auth import GuardMiddleware
from app.config import DEV_PEPPER, Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.embedding import make_embedder
from app.mcp_server import build_mcp, transport_security
from app.routers import audit as audit_routes
from app.routers import auth as auth_routes
from app.routers import documents as document_routes
from app.routers import groups as group_routes
from app.routers import search as search_routes
from app.routers import tokens as token_routes
from app.routers import workspaces as workspace_routes
from app.services.derive import run_worker
from app.services.intake import UploadGate


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
    embedder = make_embedder(settings)
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    mcp = build_mcp(settings, sessionmaker)
    mcp_asgi = mcp.streamable_http_app(transport_security=transport_security(settings))
    static_dir = Path(settings.static_dir) if settings.static_dir else None
    spa = SpaFiles(directory=static_dir, html=True) if static_dir and static_dir.is_dir() else None

    public_host = urlsplit(settings.public_origin).netloc

    async def root(scope, receive, send):
        # /mcp goes to the MCP server; everything else outside /api is the UI (KTD12).
        path = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/") or spa is None:
            return await mcp_asgi(scope, receive, send)
        host = dict(scope["headers"]).get(b"host", b"").decode()
        if scope["method"] in ("GET", "HEAD") and host != public_host:
            # The API refuses any other Origin, so a page loaded from an alias such as
            # 127.0.0.1 could never sign in; send the browser to the one public origin.
            query = scope.get("query_string", b"").decode()
            target = settings.public_origin + path + (f"?{query}" if query else "")
            return await RedirectResponse(target, status_code=307)(scope, receive, send)
        return await spa(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # A mounted sub-app's lifespan does not run, so the host runs the MCP session manager.
        worker = asyncio.create_task(run_worker(sessionmaker, app.state.embedder, settings))
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            await engine.dispose()

    app = FastAPI(title="Lattice", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.embedder = embedder
    # Added first so it runs inside the guard: the bearer and Origin checks still come first.
    app.add_middleware(UploadGate, settings=settings)
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
