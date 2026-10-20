"""AI settings API (U4): managers connect, change and disconnect; every member sees the state."""

import json
import uuid

import httpx
import psycopg
import pytest
from pydantic import SecretStr

from app.ai import keys
from app.config import DEV_AI_KEY, AiModel, AiProvider
from app.main import create_app
from tests.conftest import plain_dsn
from tests.helpers import create_workspace, join, seed_turn, signed_in

KEY = "sk-test-SECRET-key-0001"
KEY2 = "sk-test-SECRET-key-0002"
REJECTED = "sk-test-SECRET-rejected"
MODEL = {"context_tokens": 8000, "max_output_tokens": 500}
PROVIDERS = [
    AiProvider(
        id="acme",
        label="Acme",
        base_url="https://acme.test/v1",
        models=[AiModel(id="acme-large", label="Large", **MODEL)],
    ),
    AiProvider(
        id="beta",
        label="Beta",
        base_url="https://beta.test/v1",
        models=[
            AiModel(id="beta-1", label="Beta 1", **MODEL),
            AiModel(id="beta-2", label="Beta 2", **MODEL),
        ],
    ),
]


@pytest.fixture(scope="module")
def ai_settings(settings):
    return settings.model_copy(
        update={"ai_providers": PROVIDERS, "ai_key_encryption_keys": [SecretStr(DEV_AI_KEY)]}
    )


@pytest.fixture(scope="module")
def app(ai_settings):
    return create_app(ai_settings)


@pytest.fixture
def calls(app) -> list[httpx.Request]:
    """Every provider request; the provider rejects REJECTED with a 401 and accepts the rest."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers["Authorization"] == f"Bearer {REJECTED}":
            return httpx.Response(401)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    app.state.ai_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return seen


def url(ws: str) -> str:
    return f"/api/workspaces/{ws}/ai/settings"


def connect(provider="acme", model="acme-large", retention=30, key=KEY, **extra) -> dict:
    body = {"provider_id": provider, "model": model, "retention_days": retention, **extra}
    return body | ({"api_key": key} if key else {})


def stored(pg, ws: str):
    with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
        return conn.execute(
            "SELECT provider_id, model, encrypted_key, key_status, retention_days "
            "FROM ai_settings WHERE workspace_id = %s",
            (ws,),
        ).fetchone()


def audit(pg, ws: str) -> list[tuple]:
    with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
        return conn.execute(
            "SELECT actor_user_id::text, action, outcome, detail FROM audit_events "
            "WHERE workspace_id = %s AND action = 'manage_ai_settings' ORDER BY id",
            (ws,),
        ).fetchall()


def all_audit_text(pg, ws: str) -> str:
    with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
        rows = conn.execute("SELECT * FROM audit_events WHERE workspace_id = %s", (ws,))
        return repr(rows.fetchall())


def no_key_in(*texts: str) -> None:
    for text in texts:
        for key in (KEY, KEY2, REJECTED):
            assert key not in text


def age_conversation(pg, conversation_id: str, days: int) -> None:
    with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
        conn.execute(
            "UPDATE conversations SET last_turn_at = now() - make_interval(days => %s) "
            "WHERE id = %s",
            (days, conversation_id),
        )


async def test_ae5_a_demoted_admins_key_is_replaced_by_the_owner(ai_settings, pg, app, calls):
    async with signed_in(ai_settings, app) as owner, signed_in(ai_settings, app) as admin:
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        connected = await admin.put(url(ws), json=connect())
        assert connected.status_code == 200, connected.text
        # One fixed-prompt check with no workspace text, sent with the new key (KTD9).
        (check,) = calls
        assert check.url == "https://acme.test/v1/chat/completions"
        assert check.headers["Authorization"] == f"Bearer {KEY}"
        assert json.loads(check.content)["messages"] == [
            {"role": "user", "content": "Reply with the word OK."}
        ]

        demoted = await owner.patch(
            f"/api/workspaces/{ws}/members/{admin.user_id}", json={"role": "member"}
        )
        assert demoted.status_code == 200
        assert (await admin.put(url(ws), json=connect(key=KEY2))).status_code == 404
        replaced = await owner.put(url(ws), json=connect(key=KEY2))
        assert replaced.status_code == 200

        provider, model, sealed, status, _ = stored(pg, ws)
        assert (provider, model, status) == ("acme", "acme-large", "ok")
        assert keys.decrypt(ai_settings, uuid.UUID(ws), "acme", bytes(sealed)) == KEY2
        rows = audit(pg, ws)
        assert [(r[0], r[2], r[3]["key_replaced"]) for r in rows if r[2] == "allow"] == [
            (admin.user_id, "allow", True),
            (owner.user_id, "allow", True),
        ]
        no_key_in(connected.text, replaced.text, all_audit_text(pg, ws))


async def test_a_members_save_is_refused_and_changes_nothing(ai_settings, pg, app, calls):
    async with signed_in(ai_settings, app) as owner, signed_in(ai_settings, app) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        refused = await member.put(url(ws), json=connect())
        assert (refused.status_code, refused.json()) == (
            404,
            {"detail": "Not found or not permitted."},
        )
        assert stored(pg, ws) is None and calls == []
        assert [(r[0], r[2]) for r in audit(pg, ws)] == [(member.user_id, "deny")]
        assert (await member.delete(url(ws))).status_code == 404


async def test_members_see_the_provider_and_only_managers_see_the_key_status(
    ai_settings, pg, app, calls
):
    async with (
        signed_in(ai_settings, app) as owner,
        signed_in(ai_settings, app) as admin,
        signed_in(ai_settings, app) as member,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        await join(owner, ws, member)
        off = (await member.get(url(ws))).json()
        assert (off["enabled"], off["provider_id"], off["retention_days"]) == (False, None, 30)
        assert [p["id"] for p in off["providers"]] == ["acme", "beta"]
        assert off["providers"][1]["models"] == [
            {"id": "beta-1", "label": "Beta 1"},
            {"id": "beta-2", "label": "Beta 2"},
        ]
        await owner.put(url(ws), json=connect())
        with psycopg.connect(plain_dsn(pg["superuser"])) as conn:  # a turn's 401 (R30, AE4)
            conn.execute(
                "UPDATE ai_settings SET key_status = 'rejected' WHERE workspace_id = %s", (ws,)
            )

        seen = (await member.get(url(ws))).json()
        assert (seen["enabled"], seen["provider_id"], seen["model"]) == (True, "acme", "acme-large")
        assert "key_status" not in seen
        managed = await admin.get(url(ws))
        assert managed.json()["key_status"] == "rejected"
        no_key_in(managed.text, json.dumps(seen))


async def test_a_key_the_provider_rejects_is_not_stored(ai_settings, pg, app, calls):
    async with signed_in(ai_settings, app) as owner:
        ws = await create_workspace(owner)
        refused = await owner.put(url(ws), json=connect(key=REJECTED))
        assert refused.status_code == 400
        assert "rejected" in refused.json()["detail"]
        assert stored(pg, ws) is None and len(calls) == 1
        no_key_in(refused.text, all_audit_text(pg, ws))


async def test_changing_the_provider_needs_a_new_key(ai_settings, pg, app, calls):
    async with signed_in(ai_settings, app) as owner:
        ws = await create_workspace(owner)
        await owner.put(url(ws), json=connect())
        keyless = await owner.put(url(ws), json=connect("beta", "beta-1", key=None))
        assert keyless.status_code == 400 and stored(pg, ws)[0] == "acme"

        moved = await owner.put(url(ws), json=connect("beta", "beta-1", key=KEY2))
        assert moved.status_code == 200 and calls[-1].headers["Authorization"] == f"Bearer {KEY2}"
        provider, model, sealed, _, _ = stored(pg, ws)
        assert (provider, model) == ("beta", "beta-1")
        assert keys.decrypt(ai_settings, uuid.UUID(ws), "beta", bytes(sealed)) == KEY2

        # A model change on the same provider is checked with the stored key.
        checks = len(calls)
        remodelled = await owner.put(url(ws), json=connect("beta", "beta-2", key=None))
        assert remodelled.status_code == 200 and stored(pg, ws)[1] == "beta-2"
        assert len(calls) == checks + 1 and calls[-1].headers["Authorization"] == f"Bearer {KEY2}"
        assert json.loads(calls[-1].content)["model"] == "beta-2"
        assert audit(pg, ws)[-1][3]["changed"] == {"model": ["beta-1", "beta-2"]}
        assert audit(pg, ws)[-1][3]["key_replaced"] is False


async def test_shortening_retention_deletes_only_with_the_confirmed_count(
    ai_settings, pg, app, calls
):
    async with signed_in(ai_settings, app) as owner:
        ws = await create_workspace(owner)
        await owner.put(url(ws), json=connect())
        old = [seed_turn(pg, ws, owner.user_id, [])[0] for _ in range(2)]
        fresh, _ = seed_turn(pg, ws, owner.user_id, [])
        for conversation in old:
            age_conversation(pg, conversation, 20)
        checks = len(calls)

        unconfirmed = await owner.put(url(ws), json=connect(retention=10, key=None))
        assert unconfirmed.status_code == 409
        assert unconfirmed.json()["conversations_to_delete"] == 2
        wrong = await owner.put(url(ws), json=connect(retention=10, key=None, confirm_deletions=1))
        assert wrong.status_code == 409 and stored(pg, ws)[4] == 30

        saved = await owner.put(url(ws), json=connect(retention=10, key=None, confirm_deletions=2))
        assert saved.status_code == 200 and stored(pg, ws)[4] == 10
        with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
            left = conn.execute("SELECT id::text FROM conversations WHERE workspace_id = %s", (ws,))
            assert [c for (c,) in left] == [fresh]
        detail = audit(pg, ws)[-1][3]
        assert (detail["changed"], detail["conversations_deleted"]) == (
            {"retention_days": [30, 10]},
            2,
        )
        assert len(calls) == checks  # a retention-only save calls no provider (KTD9)


async def test_only_listed_providers_and_models_are_accepted(ai_settings, pg, app, calls):
    async with signed_in(ai_settings, app) as owner:
        ws = await create_workspace(owner)
        for provider, model in (("other", "acme-large"), ("acme", "beta-1")):
            refused = await owner.put(url(ws), json=connect(provider, model))
            assert refused.status_code == 400
        assert stored(pg, ws) is None and calls == []


@pytest.mark.parametrize("days", [0, 366])
async def test_retention_outside_one_to_365_days_is_refused(ai_settings, pg, app, calls, days):
    async with signed_in(ai_settings, app) as owner:
        ws = await create_workspace(owner)
        refused = await owner.put(url(ws), json=connect(retention=days))
        assert refused.status_code == 422 and stored(pg, ws) is None and calls == []
        no_key_in(refused.text)


async def test_a_malformed_body_never_echoes_the_key(ai_settings, pg, app, calls):
    async with signed_in(ai_settings, app) as owner:
        ws = await create_workspace(owner)
        for body in (
            {"api_key": KEY, "retention_days": "soon"},  # missing fields echo the whole body
            {"api_key": [KEY], "provider_id": "acme", "model": "acme-large", "retention_days": 30},
            [KEY],
        ):
            refused = await owner.put(url(ws), json=body)
            assert refused.status_code == 422, refused.text
            no_key_in(refused.text)
        broken = await owner.put(
            url(ws), content=f'{{"api_key": "{KEY}",', headers={"Content-Type": "application/json"}
        )
        assert broken.status_code == 422
        no_key_in(broken.text)
        assert stored(pg, ws) is None and calls == []


async def test_disconnecting_removes_the_key_and_keeps_conversations(ai_settings, pg, app, calls):
    async with signed_in(ai_settings, app) as owner:
        ws = await create_workspace(owner)
        await owner.put(url(ws), json=connect(retention=60))
        conversation, _ = seed_turn(pg, ws, owner.user_id, [])
        assert (await owner.delete(url(ws))).status_code == 204
        assert stored(pg, ws) == (None, None, None, "ok", 60)
        with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
            kept = conn.execute("SELECT id::text FROM conversations WHERE workspace_id = %s", (ws,))
            assert [c for (c,) in kept] == [conversation]
        view = (await owner.get(url(ws))).json()
        assert (view["enabled"], view["provider_id"], view["key_status"]) == (False, None, None)
        assert audit(pg, ws)[-1][3] == {"op": "disconnect", "provider_id": "acme"}
