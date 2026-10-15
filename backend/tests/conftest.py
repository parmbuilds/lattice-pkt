import os
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
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings
from testcontainers.community.postgres import PostgresContainer

from app.config import Settings

# Property-test budgets (KTD3): a quick local profile and a larger CI profile.
for _name, _examples, _steps in (("dev", 8, 20), ("ci", 50, 30)):
    hypothesis_settings.register_profile(
        _name,
        max_examples=_examples,
        stateful_step_count=_steps,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    )
hypothesis_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))

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
        "pgvector/pgvector:pg18",
        username="postgres",
        password="postgres",
        dbname="lattice",
        driver=None,
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
        embedder="fake",
        derive_inline=True,  # ASGITransport never runs the lifespan, so no worker (KTD3)
    )


def pytest_collection_modifyitems(config, items):
    # The real-model smoke needs the cached model, so it runs only when asked for by marker.
    if "benchmark_model" in (config.option.markexpr or ""):
        return
    skip = pytest.mark.skip(reason="needs the cached model; select with -m benchmark_model")
    for item in items:
        if item.get_closest_marker("benchmark_model"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _property_suite_uses_the_fake_embedder(request):
    # Property runs must stay deterministic and offline, so they never load the real model.
    if request.node.get_closest_marker("property"):
        assert request.getfixturevalue("settings").embedder == "fake"


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
