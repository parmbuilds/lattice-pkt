import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer

from app.config import Settings

BACKEND = Path(__file__).resolve().parents[1]
ROLES_SQL = BACKEND.parent / "db" / "init" / "01-roles.sql"


def _bootstrap_roles(superuser_dsn: str) -> None:
    # The init script targets psql; drop its \connect line since we are already on `lattice`.
    sql = "\n".join(
        line for line in ROLES_SQL.read_text().splitlines() if not line.startswith("\\connect")
    )
    with psycopg.connect(superuser_dsn, autocommit=True) as conn:
        conn.execute(sql)


@pytest.fixture(scope="session")
def pg() -> Iterator[dict[str, str]]:
    with PostgresContainer(
        "postgres:18", username="postgres", password="postgres", dbname="lattice", driver=None
    ) as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        _bootstrap_roles(f"postgresql://postgres:postgres@{host}:{port}/lattice")
        urls = {
            "app": f"postgresql+psycopg://lattice_app:lattice_app_dev@{host}:{port}/lattice",
            "owner": f"postgresql+psycopg://lattice_owner:lattice_owner_dev@{host}:{port}/lattice",
            "superuser": f"postgresql://postgres:postgres@{host}:{port}/lattice",
        }
        _migrate(urls["owner"])
        yield urls


def _migrate(owner_url: str) -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", owner_url)
    command.upgrade(config, "head")


def plain_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="session")
def settings(pg: dict[str, str]) -> Settings:
    return Settings(
        database_url=pg["app"],
        migration_database_url=pg["owner"],
        secret_pepper="test-pepper-0123456789",
        public_origin="http://127.0.0.1:8000",
        insecure_dev_cookies=True,
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def live_server(app) -> Iterator[str]:
    """Run the ASGI app on a real local port so MCP calls go through HTTP auth middleware."""
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("live server did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
