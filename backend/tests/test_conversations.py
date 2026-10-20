"""Conversations API and retention (U6): own rows only, expiry reads as absent, Ask in history
(R15, R18-R21, R31; KTD11, KTD13)."""

import json

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.ai import retention
from app.db import make_sessionmaker
from app.main import create_app
from tests.endpoint_world import sql
from tests.helpers import create_workspace, join, seed_summary, seed_turn, signed_in
from tests.test_answering import _approved, _group, _turn

NOT_FOUND = {"detail": "Not found or not permitted."}


@pytest.fixture(scope="module")
def app(settings):
    return create_app(settings)


@pytest.fixture
async def sessions(settings):
    engine = create_async_engine(settings.database_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


def url(ws: str, tail: str = "") -> str:
    return f"/api/workspaces/{ws}/conversations{tail}"


async def _create(client, ws: str, title: str = "Checkout question") -> str:
    response = await client.post(url(ws), json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _idle(pg, conversation: str, days: int) -> None:
    sql(
        pg,
        "UPDATE conversations SET last_turn_at = now() - make_interval(days => %s) "
        "WHERE id = %s RETURNING id",
        days,
        conversation,
    )


def _retention(pg, ws: str, days: int) -> None:
    sql(
        pg,
        "INSERT INTO ai_settings (workspace_id, provider_id, model, encrypted_key, retention_days)"
        " VALUES (%s, 'fake', 'fake-1', 'sealed', %s) ON CONFLICT (workspace_id) DO UPDATE"
        " SET retention_days = EXCLUDED.retention_days RETURNING workspace_id",
        ws,
        days,
    )


def _ask_rows(pg, ws: str, user_id: str, conversation: str, question: str) -> None:
    """The two audit rows the turn route writes for one turn: "sent", then the outcome."""
    for detail in (
        {"conversation_id": conversation, "query": question, "count": 1},
        {"conversation_id": conversation, "outcome": "answered"},
    ):
        sql(
            pg,
            "INSERT INTO audit_events (workspace_id, actor_user_id, channel, action, outcome, "
            "rule_id, detail) VALUES (%s, %s, 'ui', 'ask', 'allow', 'ask.member', %s) RETURNING id",
            ws,
            user_id,
            json.dumps(detail),
        )


def _exists(pg, conversation: str) -> bool:
    return bool(sql(pg, "SELECT 1 FROM conversations WHERE id = %s", conversation))


async def test_conversations_are_visible_only_to_the_person_who_started_them(settings, app, pg):
    """R18, R19: another member and the owner both get not found, for read and delete."""
    async with (
        signed_in(settings, app) as owner,
        signed_in(settings, app) as mei,
        signed_in(settings, app) as sam,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, mei)
        await join(owner, ws, sam)
        mine = await _create(mei, ws, "  How do I restart checkout?  ")
        theirs = await _create(owner, ws, "Owner's own")

        listed = (await mei.get(url(ws))).json()["conversations"]
        assert [(c["id"], c["title"]) for c in listed] == [(mine, "How do I restart checkout?")]
        assert [c["id"] for c in (await owner.get(url(ws))).json()["conversations"]] == [theirs]
        opened = (await mei.get(url(ws, f"/{mine}"))).json()
        assert (opened["id"], opened["turns"], opened["left_out"]) == (mine, [], 0)

        for client in (sam, owner):
            for method in ("GET", "DELETE"):
                response = await client.request(method, url(ws, f"/{mine}"))
                assert (response.status_code, response.json()) == (404, NOT_FOUND)
        assert _exists(pg, mine)

        blank = await mei.post(url(ws), json={"title": "   "})
        assert blank.status_code == 400


async def test_reopening_after_leaving_the_group_hides_turns_and_counts_them(settings, app, pg):
    """Covers AE2."""
    async with signed_in(settings, app) as owner, signed_in(settings, app) as sam:
        ws = await create_workspace(owner)
        await join(owner, ws, sam)
        payments = await _group(owner, ws, "Payments", sam.user_id)
        ops = await _group(owner, ws, "Ops", sam.user_id)
        incident_body, runbook_body = "Checkout failed at noon.", "Restart the checkout pods."
        incident = await _approved(
            owner, ws, "Incident 2026-09-14: checkout outage", incident_body, payments
        )
        runbook = await _approved(owner, ws, "Checkout runbook", runbook_body, ops)
        conversation = await _create(sam, ws)
        _turn(pg, ws, conversation, 1, incident, incident_body)
        _turn(pg, ws, conversation, 2, runbook, runbook_body)

        removed = await owner.delete(
            f"/api/workspaces/{ws}/groups/{payments}/members/{sam.user_id}"
        )
        assert removed.status_code == 204
        response = await sam.get(url(ws, f"/{conversation}"))
        approver = (await owner.get("/api/auth/me")).json()["user"]["name"]

    assert response.status_code == 200
    opened = response.json()
    assert opened["left_out"] == 1
    ((turn,),) = [opened["turns"]]
    assert (turn["ordinal"], turn["question"], turn["outcome"]) == (2, "question 2", "answered")
    assert [(p["title"], p["text"]) for p in turn["passages"]] == [
        ("Checkout runbook", runbook_body)
    ]
    # A reopened passage shows its approval, as the live answer does (R11).
    assert turn["passages"][0]["approved_by"] == approver
    assert turn["passages"][0]["approved_at"]
    assert "Incident" not in response.text and incident_body not in response.text


async def test_an_expired_conversation_reads_as_absent_and_the_sweep_deletes_it(
    settings, app, pg, sessions
):
    """KTD11: with no settings row the default 30 days applies."""
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        expired, fresh = await _create(owner, ws, "Old"), await _create(owner, ws, "New")
        _idle(pg, expired, 31)

        assert (await owner.get(url(ws, f"/{expired}"))).status_code == 404
        assert (await owner.delete(url(ws, f"/{expired}"))).status_code == 404
        listed = (await owner.get(url(ws))).json()["conversations"]
        assert [c["id"] for c in listed] == [fresh]
        assert _exists(pg, expired)

        await retention.sweep(sessions, settings)
        assert not _exists(pg, expired) and _exists(pg, fresh)


async def test_a_shorter_retention_applies_to_existing_conversations(settings, app, pg):
    """R20: 30 days down to 1 hides a conversation idle for 2 days."""
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        _retention(pg, ws, 30)
        conversation = await _create(owner, ws)
        _idle(pg, conversation, 2)
        assert (await owner.get(url(ws, f"/{conversation}"))).status_code == 200

        _retention(pg, ws, 1)
        assert (await owner.get(url(ws, f"/{conversation}"))).status_code == 404


async def test_a_saved_conversation_stays_readable_after_disconnect(settings, app, pg):
    """R31."""
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        _retention(pg, ws, 30)
        body = "Restart the checkout pods."
        runbook = await _approved(owner, ws, "Checkout runbook", body)
        conversation = await _create(owner, ws)
        _turn(pg, ws, conversation, 1, runbook, body)

        assert (await owner.delete(f"/api/workspaces/{ws}/ai/settings")).status_code == 204
        response = await owner.get(url(ws, f"/{conversation}"))

    assert response.status_code == 200
    assert [t["ordinal"] for t in response.json()["turns"]] == [1]


async def test_ask_turns_show_in_history_while_their_conversation_lives(settings, app, pg):
    """KTD13: the "sent" row is listed as Ask; deleting or expiring the conversation removes it."""
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        kept, expired = await _create(owner, ws), await _create(owner, ws)
        _idle(pg, expired, 31)
        _ask_rows(pg, ws, owner.user_id, expired, "An expired question")
        _ask_rows(pg, ws, owner.user_id, kept, "When do pods restart?")
        await owner.get(f"/api/workspaces/{ws}/search", params={"q": "checkout"})
        history = f"/api/workspaces/{ws}/search/history"

        searches = (await owner.get(history)).json()["searches"]
        assert [(s["source"], s["query"], s["conversation_id"]) for s in searches] == [
            ("search", "checkout", None),
            ("ask", "When do pods restart?", kept),
        ]
        assert searches[1]["count"] == 1

        assert (await owner.delete(url(ws, f"/{kept}"))).status_code == 204
        after = (await owner.get(history)).json()["searches"]
        assert [s["source"] for s in after] == ["search"]


async def test_deleting_a_conversation_removes_its_turns_summaries_and_passages(settings, app, pg):
    """R21."""
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        doc = await _approved(owner, ws, "Checkout runbook", "Restart the checkout pods.")
        cited = [(doc["id"], doc["pending_version_id"])]
        conversation, turn = seed_turn(pg, ws, owner.user_id, cited)
        summary = seed_summary(pg, ws, conversation, cited)

        assert (await owner.delete(url(ws, f"/{conversation}"))).status_code == 204

    for table, column, key in (
        ("conversations", "id", conversation),
        ("turns", "conversation_id", conversation),
        ("turn_passages", "turn_id", turn),
        ("conversation_summaries", "conversation_id", conversation),
        ("summary_sources", "summary_id", summary),
    ):
        assert sql(pg, f"SELECT count(*) FROM {table} WHERE {column} = %s", key) == [(0,)]  # noqa: S608
