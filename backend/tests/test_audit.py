import uuid

import httpx
import psycopg
import pytest
from starlette.routing import Mount

import app.audit as audit_module
from app.audit import authorize
from app.auth import Member
from app.db import DB
from app.main import create_app
from app.models import AuditEvent, Group
from app.policy import Action
from tests.conftest import plain_dsn
from tests.helpers import api_client, seed_workspace, sign_up_and_in, user_id


def _app_with_probe(settings):
    """The real app plus one route that makes a business change and records a decision."""
    app = create_app(settings)

    @app.post("/api/workspaces/{workspace_id}/probe")
    async def probe(subject: Member, db: DB, name: str, note: str = "") -> dict:
        db.add(Group(workspace_id=subject.workspace_id, name=name))
        await authorize(db, subject, Action.MANAGE_GROUPS, detail={"note": note})
        return {"ok": True}

    # Keep the catch-all "/" mount last so the probe route is reachable.
    mounts = [r for r in app.router.routes if isinstance(r, Mount)]
    for mount in mounts:
        app.router.routes.remove(mount)
        app.router.routes.append(mount)
    return app


@pytest.fixture
async def admin_client(settings, pg):
    app = _app_with_probe(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url=settings.public_origin,
        headers={"Origin": settings.public_origin},
    ) as client:
        csrf = await sign_up_and_in(client)
        client.headers["X-CSRF-Token"] = csrf
        workspace_id = seed_workspace(pg, {await user_id(client): "admin"})
        yield client, workspace_id
    await app.state.engine.dispose()


def _group_exists(pg, name: str) -> bool:
    with psycopg.connect(plain_dsn(pg["app"])) as conn:
        return conn.execute("SELECT 1 FROM groups WHERE name = %s", (name,)).fetchone() is not None


async def test_an_action_whose_audit_insert_fails_is_rolled_back(admin_client, pg, monkeypatch):
    client, workspace_id = admin_client

    def broken_event(**fields):
        return AuditEvent(**{**fields, "channel": "not-a-channel"})  # violates a CHECK

    monkeypatch.setattr(audit_module, "AuditEvent", broken_event)
    name = f"g-{uuid.uuid4()}"
    response = await client.post(f"/api/workspaces/{workspace_id}/probe", params={"name": name})
    assert response.status_code == 500
    assert not _group_exists(pg, name)


async def test_an_allowed_action_and_its_audit_row_commit_together(admin_client, pg):
    client, workspace_id = admin_client
    name = f"g-{uuid.uuid4()}"
    response = await client.post(
        f"/api/workspaces/{workspace_id}/probe", params={"name": name, "note": "x" * 1000}
    )
    assert response.status_code == 200
    assert _group_exists(pg, name)
    events = (
        await client.get(
            f"/api/workspaces/{workspace_id}/audit", params={"action": "manage_groups"}
        )
    ).json()["events"]
    assert events[0]["outcome"] == "allow"
    assert len(events[0]["detail"]["note"]) == 256  # stored text is bounded


async def test_a_denial_commits_its_audit_row_and_returns_the_generic_response(settings, pg):
    async with api_client(settings) as admin, api_client(settings) as member:
        admin.headers["X-CSRF-Token"] = await sign_up_and_in(admin)
        await sign_up_and_in(member)
        member_id = await user_id(member)
        workspace_id = seed_workspace(pg, {await user_id(admin): "admin", member_id: "member"})

        denied = await member.get(f"/api/workspaces/{workspace_id}/audit")
        missing = await member.get(f"/api/workspaces/{uuid.uuid4()}/audit")
        events = (
            await admin.get(
                f"/api/workspaces/{workspace_id}/audit",
                params={"outcome": "deny", "user_id": member_id},
            )
        ).json()["events"]

    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()  # R29: denied looks exactly like missing
    assert [e["rule_id"] for e in events] == ["view_audit.not_permitted"]


async def test_requests_from_outside_the_workspace_are_not_written_to_its_trail(settings, pg):
    async with api_client(settings) as admin, api_client(settings) as outsider:
        admin.headers["X-CSRF-Token"] = await sign_up_and_in(admin)
        await sign_up_and_in(outsider)
        outsider_id = await user_id(outsider)
        workspace_id = seed_workspace(pg, {await user_id(admin): "admin"})
        assert (await outsider.get(f"/api/workspaces/{workspace_id}/audit")).status_code == 404
        events = (
            await admin.get(
                f"/api/workspaces/{workspace_id}/audit", params={"user_id": outsider_id}
            )
        ).json()["events"]
    assert events == []
