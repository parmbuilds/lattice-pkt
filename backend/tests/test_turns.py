"""U9: streaming Ask turns in three steps, with both audit rows and the in-flight claim (R7, R11,
R13, R23, R26, R27, R29, R31; KTD5, KTD7, KTD9, KTD10; AE4, AE6)."""

import asyncio
import json

import httpx
import psycopg
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from app.ai import retention, turns
from app.ai.provider import FakeProvider
from app.config import DEV_AI_KEY, AiModel, AiProvider
from app.db import make_sessionmaker
from app.main import create_app
from tests.conftest import plain_dsn
from tests.endpoint_grounds import AI_PROVIDER, sse
from tests.endpoint_world import sql
from tests.helpers import create_workspace, join, signed_in
from tests.test_answering import _approved, _group

QUESTION = "How do I restart checkout?"
DOCS = [
    ("Checkout runbook", "Restart the checkout pods first."),
    ("Checkout drain", "Drain checkout traffic before a restart."),
    ("Checkout timing", "Checkout restarts take two minutes."),
]
AI_KEYS = {"ai_key_encryption_keys": [SecretStr(DEV_AI_KEY)]}


def _app(settings, provider: AiProvider = AI_PROVIDER):
    # A short read timeout, so a provider call that never ends fails fast.
    update = {"ai_providers": [provider], "ai_read_timeout_seconds": 2.0, **AI_KEYS}
    return create_app(settings.model_copy(update=update))


@pytest.fixture(scope="module")
def app(settings):
    return _app(settings)


@pytest.fixture
async def sessions(settings):
    engine = create_async_engine(settings.database_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


class Steered:
    """The fake provider, recording each request; each stream can hold after a line count (or
    at its end) until the test releases it, and `probe` runs as each request arrives."""

    def __init__(self, inner, hold_after: int | None = None, hold_at_end: bool = False):
        self.inner, self.hold_after, self.hold_at_end = inner, hold_after, hold_at_end
        self.requests: list[list[dict]] = []
        self.gates: dict[int, tuple[asyncio.Event, asyncio.Event]] = {}
        self.probe = lambda: None

    def _gate(self, n: int) -> tuple[asyncio.Event, asyncio.Event]:
        return self.gates.setdefault(n, (asyncio.Event(), asyncio.Event()))

    async def held(self, n: int = 0) -> None:
        await asyncio.wait_for(self._gate(n)[0].wait(), 10)

    def release(self, n: int = 0) -> None:
        self._gate(n)[1].set()

    async def _hold(self, n: int) -> None:
        held, release = self._gate(n)
        held.set()
        await release.wait()

    async def stream(self, messages, *, max_tokens):
        self.probe()
        n, lines = len(self.requests), 0
        self.requests.append(messages)
        async for chunk in self.inner.stream(messages, max_tokens=max_tokens):
            yield chunk
            lines += chunk.count("\n")
            if lines == self.hold_after and "\n" in chunk:
                await self._hold(n)
        if self.hold_at_end:
            await self._hold(n)

    async def complete(self, messages, *, max_tokens):
        return await self.inner.complete(messages, max_tokens=max_tokens)


@pytest.fixture
def steer(monkeypatch, app):
    def install(**hold) -> Steered:
        provider = Steered(FakeProvider(app.state.ai_client), **hold)
        monkeypatch.setattr(turns, "make_provider", lambda *args: provider)
        return provider

    return install


def url(ws: str, conversation: str = "", tail: str = "") -> str:
    return f"/api/workspaces/{ws}/conversations{conversation and '/' + conversation}{tail}"


async def _connected(owner, documents=DOCS) -> tuple[str, list[dict], str]:
    """A workspace with the fake provider connected, approved documents and a conversation."""
    ws = await create_workspace(owner)
    saved = await owner.put(
        f"/api/workspaces/{ws}/ai/settings",
        json={"provider_id": "fake", "model": "fake-1", "retention_days": 30, "api_key": "sk-x"},
    )
    assert saved.status_code == 200, saved.text
    docs = [await _approved(owner, ws, title, body) for title, body in documents]
    return ws, docs, await _conversation(owner, ws)


async def _conversation(client, ws) -> str:
    return (await client.post(url(ws), json={"title": "Checkout"})).json()["id"]


async def _ask(client, ws, conversation, question=QUESTION) -> httpx.Response:
    async with asyncio.timeout(15):  # a stream nobody releases fails instead of hanging
        return await client.post(url(ws, conversation, "/turns"), json={"question": question})


def _names(response) -> list[str]:
    return [name for name, _ in sse(response.text)]


def _turns(pg, conversation) -> list[tuple]:
    query = "SELECT ordinal, outcome FROM turns WHERE conversation_id = %s ORDER BY ordinal"
    return sql(pg, query, conversation)


def _ask_rows(pg, ws) -> list[dict]:
    query = "SELECT detail FROM audit_events WHERE workspace_id = %s AND action = 'ask' ORDER BY id"
    return [detail for (detail,) in sql(pg, query, ws)]


def _outcomes(pg, ws) -> dict[int, list[str]]:
    """Each turn's outcome rows; every "sent" row must have exactly one (R23)."""
    rows = _ask_rows(pg, ws)
    sent = [r["turn"] for r in rows if "query" in r]
    found = {n: [r["outcome"] for r in rows if "outcome" in r and r["turn"] == n] for n in sent}
    assert all(len(o) == 1 for o in found.values()), found
    return {n: o[0] for n, o in found.items()}


def _mark(pg, conversation) -> tuple:
    query = "SELECT in_flight_turn, in_flight_at FROM conversations WHERE id = %s"
    return sql(pg, query, conversation)[0]


async def test_a_turn_streams_passages_then_sentences_then_done_and_is_saved(
    settings, app, pg, steer
):
    """Covers AE6: both audit rows hold the question, versions, provider, model and outcome.
    KTD5 step 1: the request session commits the "sent" row before the stream reaches the
    provider."""
    provider = steer()
    seen = []
    async with signed_in(settings, app) as owner:
        ws, docs, conversation = await _connected(owner)
        provider.probe = lambda: seen.append(_ask_rows(pg, ws))
        response = await _ask(owner, ws, conversation)
        reopened = (await owner.get(url(ws, conversation))).json()

    assert response.status_code == 200 and response.headers["content-type"].startswith(
        "text/event-stream"
    )
    events = sse(response.text)
    assert _names(response) == ["passages", "sentence", "sentence", "sentence", "done"]
    passages = events[0][1]["passages"]
    assert [p["id"] for p in passages] == ["P1", "P2", "P3"]
    assert {p["version_id"] for p in passages} == {d["pending_version_id"] for d in docs}
    assert all(p["approved_by"] and p["text"] for p in passages)
    sentences = [data for name, data in events if name == "sentence"]
    assert all(s["supported"] and len(s["markers"]) == 1 for s in sentences)
    assert events[-1][1] == {"outcome": "answered", "turn": 1}
    assert len(provider.requests) == 1 and len(seen) == 1

    assert _turns(pg, conversation) == [(1, "answered")]
    assert [t["sentences"] for t in reopened["turns"]] == [sentences]
    assert _mark(pg, conversation)[1] is None  # the mark is cleared

    rows = sql(
        pg,
        "SELECT returned_version_ids, detail FROM audit_events WHERE workspace_id = %s "
        "AND action = 'ask' ORDER BY id",
        ws,
    )
    (sent_versions, sent), (_, outcome) = rows
    assert seen[0] == [sent]  # committed, alone, before the provider's first request
    assert sent == {
        "conversation_id": conversation,
        "turn": 1,
        "query": QUESTION,
        "count": 3,
        "provider_id": "fake",
        "model": "fake-1",
    }
    assert sorted(map(str, sent_versions)) == sorted(p["version_id"] for p in passages)
    assert outcome == {"conversation_id": conversation, "turn": 1, "outcome": "answered"}
    assert not any(s["text"] in json.dumps(rows, default=str) for s in sentences)  # no answer text


async def test_a_withdrawal_after_the_second_sentence_discards_the_turn(settings, app, pg, steer):
    """R29: no third sentence, a discarded event, nothing saved, and no transaction is held
    while the provider streams."""
    provider = steer(hold_after=2)
    async with signed_in(settings, app) as owner:
        ws, docs, conversation = await _connected(owner)
        turn = asyncio.create_task(_ask(owner, ws, conversation))
        await provider.held()
        open_transactions = sql(
            pg,
            "SELECT count(*) FROM pg_stat_activity WHERE usename = 'lattice_app' "
            "AND state LIKE 'idle in transaction%%'",
        )
        withdrawn = await owner.post(f"/api/workspaces/{ws}/documents/{docs[2]['id']}/withdraw")
        assert withdrawn.status_code == 204
        provider.release()
        response = await turn

    assert open_transactions == [(0,)]
    assert _names(response) == ["passages", "sentence", "sentence", "discarded"]
    assert sse(response.text)[-1][1] == {"notice": turns.NOT_SAVED}
    assert _turns(pg, conversation) == []
    assert _outcomes(pg, ws) == {1: "discarded"}
    assert _mark(pg, conversation)[1] is None


async def test_leaving_the_sharing_group_mid_stream_discards_the_turn(settings, app, pg, steer):
    provider = steer(hold_after=1)
    async with signed_in(settings, app) as owner, signed_in(settings, app) as sam:
        ws, _, _ = await _connected(owner, documents=[])
        await join(owner, ws, sam)
        group = await _group(owner, ws, "Ops", sam.user_id)
        for title, body in DOCS:
            await _approved(owner, ws, title, body, group)
        conversation = await _conversation(sam, ws)
        turn = asyncio.create_task(_ask(sam, ws, conversation))
        await provider.held()
        left = await owner.delete(f"/api/workspaces/{ws}/groups/{group}/members/{sam.user_id}")
        assert left.status_code == 204
        provider.release()
        response = await turn

    assert _names(response) == ["passages", "sentence", "discarded"]
    assert _turns(pg, conversation) == []
    assert _outcomes(pg, ws) == {1: "discarded"}


def _lock_waits(pg) -> int:
    return sql(pg, "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")[0][0]


async def _until_lock_waits(pg, count: int) -> None:
    for _ in range(200):
        if _lock_waits(pg) >= count:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("no lock wait")


@pytest.mark.parametrize("order", ["withdrawn before step 3", "withdrawal waits on step 3"])
async def test_a_withdrawal_during_step_3_leaves_no_turn(settings, app, pg, steer, order):
    """KTD5 step 3: re-checked under FOR SHARE, or the waiting purge deletes the saved turn."""
    provider = steer(hold_at_end=True)
    async with signed_in(settings, app) as owner:
        ws, docs, conversation = await _connected(owner)
        withdraw = f"/api/workspaces/{ws}/documents/{docs[0]['id']}/withdraw"
        turn = asyncio.create_task(_ask(owner, ws, conversation))
        await provider.held()
        if order == "withdrawn before step 3":
            assert (await owner.post(withdraw)).status_code == 204
            provider.release()
            response = await turn
        else:
            with psycopg.connect(plain_dsn(pg["superuser"])) as blocker:
                # Holding the conversation row stops step 3 after it has locked the documents.
                blocker.execute(
                    "SELECT 1 FROM conversations WHERE id = %s FOR UPDATE", (conversation,)
                )
                provider.release()
                await _until_lock_waits(pg, 1)
                withdrawal = asyncio.create_task(owner.post(withdraw))
                await _until_lock_waits(pg, 2)
            response = await turn
            assert (await withdrawal).status_code == 204

    assert _turns(pg, conversation) == []
    if order == "withdrawn before step 3":
        assert _names(response)[-1] == "discarded"
        assert _outcomes(pg, ws) == {1: "discarded"}
    else:
        assert _names(response)[-1] == "done"
        assert _outcomes(pg, ws) == {1: "answered"}
        receipt = sql(
            pg,
            "SELECT detail FROM audit_events WHERE document_id = %s AND action = 'purge'",
            docs[0]["id"],
        )
        assert receipt[0][0]["removed"]["turns"] == 1


async def test_a_failing_step_3_still_writes_one_outcome_row(settings, app, pg, steer):
    """KTD5 step 4: rolled back, then the outcome is written in a new transaction."""
    steer()
    question = "Restart checkout? step-three-canary"
    fail = (
        "CREATE OR REPLACE FUNCTION u9_fail() RETURNS trigger LANGUAGE plpgsql AS "
        "$$BEGIN RAISE EXCEPTION 'u9 step 3'; END$$; "
        "CREATE TRIGGER u9_fail BEFORE INSERT ON turns FOR EACH ROW "
        f"WHEN (NEW.question = '{question}') EXECUTE FUNCTION u9_fail()"
    )
    async with signed_in(settings, app) as owner:
        ws, _, conversation = await _connected(owner)
        with psycopg.connect(plain_dsn(pg["superuser"]), autocommit=True) as conn:
            conn.execute(fail)
            try:
                response = await _ask(owner, ws, conversation, question)
            finally:
                conn.execute("DROP TRIGGER u9_fail ON turns; DROP FUNCTION u9_fail()")

    assert _names(response)[-1] == "discarded"
    assert _turns(pg, conversation) == []
    assert _outcomes(pg, ws) == {1: "discarded"}
    assert _mark(pg, conversation)[1] is None


async def test_a_provider_failure_shows_the_passages_and_one_notice(settings, app, pg, steer):
    """R13: the fake fails after its first line; provider failed is recorded."""
    steer()
    async with signed_in(settings, app) as owner:
        ws, _, conversation = await _connected(owner)
        response = await _ask(owner, ws, conversation, f"{QUESTION} #fail")

    assert _names(response) == ["passages", "sentence", "unavailable"]
    assert sse(response.text)[0][1]["passages"]
    assert sse(response.text)[-1][1] == {"notice": turns.UNAVAILABLE}
    assert _turns(pg, conversation) == []
    assert _outcomes(pg, ws) == {1: "provider_failed"}


async def test_a_rejected_key_is_marked_and_askers_see_the_generic_notice(settings, app, pg, steer):
    """Covers AE4."""
    steer()
    async with signed_in(settings, app) as owner, signed_in(settings, app) as mei:
        ws, _, conversation = await _connected(owner)
        await join(owner, ws, mei)
        response = await _ask(owner, ws, conversation, f"{QUESTION} #reject")
        manager_view = (await owner.get(f"/api/workspaces/{ws}/ai/settings")).json()
        member_view = (await mei.get(f"/api/workspaces/{ws}/ai/settings")).json()

    assert _names(response) == ["passages", "unavailable"]
    assert sse(response.text)[-1][1] == {"notice": turns.UNAVAILABLE}
    assert manager_view["key_status"] == "rejected" and "key_status" not in member_view
    assert _outcomes(pg, ws) == {1: "provider_failed"}


async def test_a_second_turn_while_one_is_in_flight_is_refused(settings, app, pg, steer):
    """KTD10: refused before the provider receives a request for it."""
    provider = steer(hold_after=1)
    async with signed_in(settings, app) as owner:
        ws, _, conversation = await _connected(owner)
        posted = [asyncio.create_task(_ask(owner, ws, conversation)) for _ in range(2)]
        done, pending = await asyncio.wait(posted, timeout=10, return_when="FIRST_COMPLETED")
        ((refused,),) = [[t.result() for t in done]]
        assert (refused.status_code, refused.json()) == (409, {"detail": turns.IN_PROGRESS})
        await provider.held()
        assert len(provider.requests) == 1
        provider.release()
        (answered,) = [await t for t in pending]

    assert _names(answered)[-1] == "done"
    assert _outcomes(pg, ws) == {1: "answered"}


def _dropping(app, after_sentences: int):
    """The app, with the client disconnecting once it has read `after_sentences` sentences."""

    async def dropping(scope, receive, send):
        dropped, body, requested = asyncio.Event(), [], False

        async def receive_or_drop():
            nonlocal requested
            if not requested:  # the request body may come in several messages
                message = await receive()
                requested = not message.get("more_body", False)
                return message
            await dropped.wait()
            return {"type": "http.disconnect"}

        async def watch(message):
            body.append(message.get("body", b""))
            if b"".join(body).count(b"event: sentence") >= after_sentences:
                dropped.set()
            await send(message)

        await app(scope, receive_or_drop, watch)
        # The app sent no final body message to the dropped client; end httpx's response.
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    return dropping


async def test_dropping_the_connection_abandons_the_turn_and_clears_the_mark(
    settings, app, pg, steer
):
    """KTD5 step 5: one "abandoned" row from the shielded cleanup."""
    steer(hold_after=2)
    async with signed_in(settings, app) as owner:
        ws, _, conversation = await _connected(owner)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_dropping(app, 2)),
            base_url=settings.public_origin,
            headers=owner.headers,
            cookies=owner.cookies,
        ) as dropping:
            response = await _ask(dropping, ws, conversation)

    assert _names(response) == ["passages", "sentence", "sentence"]
    assert _turns(pg, conversation) == []
    assert _outcomes(pg, ws) == {1: "abandoned"}
    assert _mark(pg, conversation)[1] is None


def _stale(pg, conversation, turn: int) -> None:
    sql(
        pg,
        "UPDATE conversations SET in_flight_turn = %s, in_flight_at = now() - interval '1 hour' "
        "WHERE id = %s RETURNING id",
        turn,
        conversation,
    )


async def test_a_stale_mark_does_not_block_and_cleanup_spares_the_newer_mark(
    settings, app, pg, steer
):
    """KTD10: the newer turn records the stale one abandoned, and the stale turn's late end
    neither saves it nor clears the newer turn's mark."""
    provider = steer(hold_after=1)
    async with signed_in(settings, app) as owner:
        ws, _, conversation = await _connected(owner)
        late = asyncio.create_task(_ask(owner, ws, conversation))
        await provider.held(0)
        _stale(pg, conversation, 1)
        newer = asyncio.create_task(_ask(owner, ws, conversation))
        await provider.held(1)
        provider.release(0)
        old = await late
        mark_after_old_ended = _mark(pg, conversation)
        provider.release(1)
        new = await newer

    assert _names(old)[-1] == "discarded" and _names(new)[-1] == "done"
    assert mark_after_old_ended[0] == 2 and mark_after_old_ended[1] is not None
    assert _turns(pg, conversation) == [(2, "answered")]
    assert _outcomes(pg, ws) == {1: "abandoned", 2: "answered"}


async def test_the_retention_sweep_abandons_a_stale_mark(settings, app, pg, steer, sessions):
    """KTD5 step 5, KTD11."""
    provider = steer(hold_after=1)
    async with signed_in(settings, app) as owner:
        ws, _, conversation = await _connected(owner)
        late = asyncio.create_task(_ask(owner, ws, conversation))
        await provider.held()
        _stale(pg, conversation, 1)
        await retention.sweep(sessions, settings)
        assert _mark(pg, conversation)[1] is None
        provider.release()
        await late

    assert _turns(pg, conversation) == []
    assert _outcomes(pg, ws) == {1: "abandoned"}


async def test_deleting_the_conversation_mid_turn_discards_it(settings, app, pg, steer):
    provider = steer(hold_after=1)
    async with signed_in(settings, app) as owner:
        ws, _, conversation = await _connected(owner)
        turn = asyncio.create_task(_ask(owner, ws, conversation))
        await provider.held()
        assert (await owner.delete(url(ws, conversation))).status_code == 204
        provider.release()
        response = await turn

    assert _names(response) == ["passages", "sentence", "discarded"]
    assert sse(response.text)[-1][1] == {"notice": turns.CONVERSATION_CHANGED}
    assert _outcomes(pg, ws) == {1: "conversation_changed"}


async def test_a_question_with_no_passages_is_not_covered_without_a_provider_call(
    settings, app, pg, steer
):
    """R27, R10."""
    provider = steer()
    async with signed_in(settings, app) as owner:
        ws, _, conversation = await _connected(owner, documents=[])
        response = await _ask(owner, ws, conversation)

    assert sse(response.text) == [
        ("passages", {"passages": [], "left_out": 0, "summary_dropped": False}),
        ("not_covered", {"notice": turns.NOT_COVERED, "turn": 1}),
    ]
    assert provider.requests == []
    assert _turns(pg, conversation) == [(1, "not_covered")]
    assert _outcomes(pg, ws) == {1: "not_covered"}


async def test_turns_are_refused_without_a_provider_and_after_disconnect(settings, app, pg):
    """R6, R31: saved conversations stay readable but cannot be continued."""
    async with signed_in(settings, app) as owner:
        bare = await create_workspace(owner)
        unconnected = await _ask(owner, bare, await _conversation(owner, bare))
        ws, _, conversation = await _connected(owner)
        assert _names(await _ask(owner, ws, conversation))[-1] == "done"
        assert (await owner.delete(f"/api/workspaces/{ws}/ai/settings")).status_code == 204
        refused = await _ask(owner, ws, conversation)
        reopened = await owner.get(url(ws, conversation))

    for response in (unconnected, refused):
        assert (response.status_code, response.json()) == (409, {"detail": turns.AI_OFF})
    assert [t["ordinal"] for t in reopened.json()["turns"]] == [1]
    assert _outcomes(pg, ws) == {1: "answered"}


async def test_a_condensed_memory_is_saved_with_its_sources(settings, pg):
    """KTD12: step 3 saves the summary and the versions it drew on."""
    tiny = AiModel(id="fake-1", label="Tiny", context_tokens=120, max_output_tokens=20)
    small = _app(settings, AI_PROVIDER.model_copy(update={"models": [tiny]}))
    async with signed_in(settings, small) as owner:
        ws, docs, conversation = await _connected(owner, documents=DOCS[:1])
        assert _names(await _ask(owner, ws, conversation))[-1] == "done"
        assert _names(await _ask(owner, ws, conversation, "Restart checkout again?"))[-1] == "done"

    ((summary, covers),) = sql(
        pg,
        "SELECT id, covers_through FROM conversation_summaries WHERE conversation_id = %s",
        conversation,
    )
    assert covers == 1
    assert sql(
        pg, "SELECT version_id::text FROM summary_sources WHERE summary_id = %s", summary
    ) == [(docs[0]["pending_version_id"],)]
    assert _outcomes(pg, ws) == {1: "answered", 2: "answered"}
