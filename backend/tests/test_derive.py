"""U5: derivation core, ledger worker and approve readiness (R10, R11, R14; KTD3, KTD4, KTD10)."""

import asyncio
import hashlib
import uuid

import psycopg

from app.embedding import FakeEmbedder
from app.main import create_app
from app.services import derive
from tests.conftest import plain_dsn
from tests.helpers import add_document, approve, create_workspace, derive_all, signed_in

NOT_READY = "This draft is still being prepared for search. Try again shortly."


def _sql(pg, query: str, *params) -> list[tuple]:
    with psycopg.connect(plain_dsn(pg["app"]), autocommit=True) as conn:
        cursor = conn.execute(query, params)
        return cursor.fetchall() if cursor.description else []


def _ledger(pg, version_id) -> list[tuple]:
    return _sql(
        pg,
        "SELECT config_hash, status, error FROM derivations WHERE version_id = %s",
        version_id,
    )


def _chunks(pg, version_id) -> list[tuple]:
    return _sql(
        pg,
        'SELECT start, "end", sha256 FROM chunks WHERE version_id = %s ORDER BY ordinal',
        version_id,
    )


def _worker_app(settings, **overrides):
    """An app as production runs it: drafts wait for the worker (derive_inline off)."""
    return create_app(settings.model_copy(update={"derive_inline": False, **overrides}))


def _docs(ws, doc_id, tail=""):
    return f"/api/workspaces/{ws}/documents/{doc_id}{tail}"


class GatedEmbedder(FakeEmbedder):
    """Blocks inside embedding until released, so tests can act while a derivation is in flight."""

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def embed_passages(self, texts):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await super().embed_passages(texts)


class BrokenEmbedder(FakeEmbedder):
    async def embed_passages(self, texts):
        raise RuntimeError("model file is corrupt")


async def test_a_typed_document_gets_paragraph_spans_that_tile_its_body(settings, pg):
    paragraphs = [f"{i} " + "word " * 99 + "end" for i in range(5)]  # 500 characters each
    body = "\n\n".join(paragraphs)
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws, title="Spans", body=body)
    version = doc["pending_version_id"]
    expected_hash = hashlib.sha256(
        f"{FakeEmbedder.config_identity};chunker={derive.CHUNKER_VERSION}".encode()
    ).hexdigest()
    assert _ledger(pg, version) == [(expected_hash, "ready", None)]
    # Two paragraphs and their separators fit under 1,200 characters; a third does not.
    chunks = _chunks(pg, version)
    assert [(start, end) for start, end, _ in chunks] == [(0, 1004), (1004, 2008), (2008, 2508)]
    assert len(body) == 2508
    for start, end, digest in chunks:
        assert digest == hashlib.sha256(body[start:end].encode()).hexdigest()


async def test_approve_waits_for_the_derivation_and_succeeds_after_one_tick(settings, pg):
    app = _worker_app(settings)
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        await derive_all(app)  # leftovers from earlier tests
        doc = await add_document(owner, ws)
        version = doc["pending_version_id"]
        refused = await owner.post(_docs(ws, doc["id"], "/approve"), json={"version_id": version})
        assert (refused.status_code, refused.json()) == (409, {"detail": NOT_READY})
        detail = (await owner.get(_docs(ws, doc["id"]))).json()
        assert detail["derivations"] == {version: {"status": "pending", "error": None}}

        assert await derive_all(app) == [uuid.UUID(version)]
        await approve(owner, ws, doc)
    assert [status for _, status, _ in _ledger(pg, version)] == ["ready"]
    assert _chunks(pg, version)


async def test_a_config_change_between_derive_and_approve_refuses_until_re_derived(settings, pg):
    """Covers the Key Decision governing R10, and R14."""
    app = create_app(settings)  # inline: the draft is ready at the current config
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)
        version = doc["pending_version_id"]
        ((old_hash, _, _),) = _ledger(pg, version)
        app.state.embedder.config_identity = f"fake-v2;{uuid.uuid4()}"
        refused = await owner.post(_docs(ws, doc["id"], "/approve"), json={"version_id": version})
        assert (refused.status_code, refused.json()) == (409, {"detail": NOT_READY})

        assert uuid.UUID(version) in await derive_all(app)
        await approve(owner, ws, doc)
    ((new_hash, status, _),) = _ledger(pg, version)  # the older-hash row is gone
    assert new_hash != old_hash and status == "ready"


async def test_a_stale_draft_still_answers_stale_not_not_ready(settings):
    app = _worker_app(settings)
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)
        await owner.put(_docs(ws, doc["id"]), json={"title": "Runbook", "body": "Newer."})
        refused = await owner.post(
            _docs(ws, doc["id"], "/approve"), json={"version_id": doc["pending_version_id"]}
        )
    assert refused.status_code == 409 and refused.json()["detail"].startswith("This draft was")


async def test_a_draft_replaced_while_embedding_is_not_written(settings, pg):
    app = _worker_app(settings)
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        await derive_all(app)
        app.state.embedder = gate = GatedEmbedder()
        doc = await add_document(owner, ws)
        v1 = doc["pending_version_id"]
        worker = asyncio.create_task(derive.tick(app.state.sessionmaker, gate, app.state.settings))
        await asyncio.wait_for(gate.entered.wait(), 5)
        # No lock is held while embedding, so the edit goes straight through.
        edited = await asyncio.wait_for(
            owner.put(_docs(ws, doc["id"]), json={"title": "Runbook", "body": "Replaced."}), 5
        )
        gate.release.set()
        assert await worker == uuid.UUID(v1)
    assert _ledger(pg, v1) == [] and _chunks(pg, v1) == []
    assert [s for _, s, _ in _ledger(pg, edited.json()["pending_version_id"])] == ["pending"]


async def test_a_withdraw_during_embedding_returns_at_once_and_leaves_no_derived_rows(settings, pg):
    app = _worker_app(settings)
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        await derive_all(app)
        app.state.embedder = gate = GatedEmbedder()
        doc = await add_document(owner, ws)
        worker = asyncio.create_task(derive.tick(app.state.sessionmaker, gate, app.state.settings))
        await asyncio.wait_for(gate.entered.wait(), 5)
        withdrawn = await asyncio.wait_for(owner.post(_docs(ws, doc["id"], "/withdraw")), 5)
        assert withdrawn.status_code == 204
        gate.release.set()
        await worker
    assert _sql(pg, "SELECT 1 FROM derivations WHERE document_id = %s", doc["id"]) == []
    assert _sql(pg, "SELECT 1 FROM chunks WHERE document_id = %s", doc["id"]) == []


async def test_a_pending_draft_with_no_ledger_row_is_derived_on_the_next_tick(settings, pg):
    app = _worker_app(settings)
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)
        version = doc["pending_version_id"]
        _sql(pg, "DELETE FROM derivations WHERE version_id = %s", version)  # pre-upgrade draft
        assert uuid.UUID(version) in await derive_all(app)
    assert [s for _, s, _ in _ledger(pg, version)] == ["ready"]


async def test_a_claim_whose_worker_died_is_retried_after_its_lease(settings, pg):
    app = _worker_app(settings)
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        await derive_all(app)
        doc = await add_document(owner, ws)
        version = uuid.UUID(doc["pending_version_id"])
        claimed = await derive.claim(app.state.sessionmaker, app.state.embedder, app.state.settings)
        assert claimed == version  # ... and the worker dies before deriving
        assert await derive_all(app) == []  # leased
        _sql(pg, "UPDATE derivations SET next_attempt_at = now() WHERE version_id = %s", version)
        assert await derive_all(app) == [version]


async def test_a_failed_embedder_marks_the_row_failed_and_is_retried_after_backoff(settings, pg):
    app = _worker_app(settings)
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        await derive_all(app)
        app.state.embedder = BrokenEmbedder()
        doc = await add_document(owner, ws)
        version = doc["pending_version_id"]
        assert await derive_all(app) == [uuid.UUID(version)]
        ((status, error, backing_off),) = _sql(
            pg,
            "SELECT status, error, next_attempt_at > now() FROM derivations WHERE version_id = %s",
            version,
        )
        assert (status, error, backing_off) == (
            "failed",
            "RuntimeError: model file is corrupt",
            True,
        )
        detail = (await owner.get(_docs(ws, doc["id"]))).json()
        assert detail["derivations"][version] == {"status": "failed", "error": error}

        app.state.embedder = FakeEmbedder()
        assert await derive_all(app) == []  # still backing off
        _sql(pg, "UPDATE derivations SET next_attempt_at = now() WHERE version_id = %s", version)
        assert await derive_all(app) == [uuid.UUID(version)]
    assert [(s, e) for _, s, e in _ledger(pg, version)] == [("ready", None)]


async def test_two_workers_never_derive_the_same_version_twice(settings, pg):
    app = _worker_app(settings)
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        await derive_all(app)
        app.state.embedder = gate = GatedEmbedder()
        doc = await add_document(owner, ws)
        ticks = [
            asyncio.create_task(derive.tick(app.state.sessionmaker, gate, app.state.settings))
            for _ in range(2)
        ]
        await asyncio.wait_for(gate.entered.wait(), 5)
        await asyncio.sleep(0.2)  # let the second worker reach its claim
        gate.release.set()
        results = await asyncio.gather(*ticks)
    assert sorted(results, key=str) == sorted([None, uuid.UUID(doc["pending_version_id"])], key=str)
    assert gate.calls == 1


async def test_the_worker_backfills_serving_versions_on_start(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)
        await approve(owner, ws, doc)
    version = doc["pending_version_id"]
    _sql(pg, "DELETE FROM derivations WHERE version_id = %s", version)  # served before upgrade

    app = _worker_app(settings, derive_poll_seconds=0.05)
    async with app.router.lifespan_context(app):
        for _ in range(200):
            if [s for _, s, _ in _ledger(pg, version)] == ["ready"]:
                break
            await asyncio.sleep(0.05)
    await app.state.engine.dispose()
    assert [s for _, s, _ in _ledger(pg, version)] == ["ready"] and _chunks(pg, version)
