import psycopg

from app.seed import seed
from tests.conftest import plain_dsn


async def test_the_demo_seed_runs_once_and_leaves_an_audit_trail(settings, pg):
    summary = await seed(settings)
    assert summary is not None
    assert await seed(settings) is None  # safe to re-run

    with psycopg.connect(plain_dsn(pg["app"])) as conn:
        states = conn.execute(
            "SELECT serving_version_id IS NOT NULL, pending_version_id IS NOT NULL, "
            "withdrawn_at IS NOT NULL FROM documents WHERE workspace_id = %s",
            (summary["workspace_id"],),
        ).fetchall()
        actions = {
            row[0]
            for row in conn.execute(
                "SELECT action FROM audit_events WHERE workspace_id = %s",
                (summary["workspace_id"],),
            )
        }
    assert (True, True, False) in states  # approved with a pending edit
    assert (True, False, True) in states  # withdrawn
    assert (False, True, False) in states  # draft only
    assert {"approve", "reject", "withdraw", "share", "accept_invite"} <= actions
