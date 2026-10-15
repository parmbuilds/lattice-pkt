"""U6: lifecycle purge, purge receipts and the bill of derivatives (R12, R13; KTD8, KTD9)."""

import asyncio

import psycopg
import pytest
from sqlalchemy.exc import IntegrityError

import app.audit as audit_module
from app.derived import DERIVED_TABLES
from app.main import create_app
from app.models import AuditEvent
from tests.conftest import plain_dsn
from tests.endpoint_world import sql
from tests.helpers import add_document, approve, create_workspace, signed_in

# A comment is hidden from the body and kept only as an excerpt in the purgeable payload.
SECRET = "PURGE-CANARY-SECRET"
FLAGGED_MD = f"# Failover\n\nSwitch regions.\n<!-- {SECRET} -->\nDone.\n".encode()
RETAINED = "Version bodies are retained: document versions are immutable."


def _docs(ws, doc_id="", tail=""):
    return f"/api/workspaces/{ws}/documents{doc_id and '/' + doc_id}{tail}"


def _derived(pg, version_id) -> dict[str, int]:
    tables = ("derivations", "chunks", "intake_payloads")
    return {
        t: sql(pg, f"SELECT count(*) FROM {t} WHERE version_id = %s", version_id)[0][0]  # noqa: S608
        for t in tables
    }


def _receipts(pg, doc_id) -> list[tuple]:
    return sql(
        pg,
        "SELECT actor_user_id, channel, action, outcome, detail FROM audit_events "
        "WHERE document_id = %s AND channel = 'system' ORDER BY id",
        doc_id,
    )


def _receipt(reason: str, derivations: int, chunks: int, payloads: int = 0) -> tuple:
    removed = {"derivations": derivations, "chunks": chunks, "intake_payloads": payloads}
    detail = {"reason": reason, "removed": removed, "note": RETAINED}
    return (None, "system", "purge", "allow", detail)


async def _upload(client, ws, data=FLAGGED_MD, **fields):
    form = {key: (None, str(value)) for key, value in fields.items()}
    response = await client.post(
        _docs(ws, tail="/upload"), files={"file": ("failover.md", data), **form}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_rejecting_a_derived_draft_purges_it_and_writes_a_receipt(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)
        version = doc["pending_version_id"]
        assert _derived(pg, version) == {"derivations": 1, "chunks": 1, "intake_payloads": 0}
        rejected = await owner.post(_docs(ws, doc["id"], "/reject"), json={"version_id": version})
        assert rejected.status_code == 204
    assert _derived(pg, version) == {"derivations": 0, "chunks": 0, "intake_payloads": 0}
    assert _receipts(pg, doc["id"]) == [_receipt("rejected", 1, 1)]


async def test_discarding_a_draft_purges_it(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)
        version = doc["pending_version_id"]
        discarded = await owner.post(_docs(ws, doc["id"], "/discard"), json={"version_id": version})
        assert discarded.status_code == 204
    assert _derived(pg, version)["chunks"] == 0
    assert _receipts(pg, doc["id"]) == [_receipt("discarded", 1, 1)]


async def test_approving_v2_purges_the_superseded_v1_and_keeps_v2(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)
        await approve(owner, ws, doc)
        assert _receipts(pg, doc["id"]) == []  # a first approval supersedes nothing
        edited = await owner.put(
            _docs(ws, doc["id"]), json={"title": "Runbook", "body": "Drain checkout."}
        )
        await approve(owner, ws, edited.json())
    v1, v2 = doc["pending_version_id"], edited.json()["pending_version_id"]
    assert _derived(pg, v1) == {"derivations": 0, "chunks": 0, "intake_payloads": 0}
    assert _derived(pg, v2) == {"derivations": 1, "chunks": 1, "intake_payloads": 0}
    assert _receipts(pg, doc["id"]) == [_receipt("superseded", 1, 1)]


async def test_an_edit_that_replaces_a_pending_draft_purges_the_old_draft(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)
        edited = await owner.put(
            _docs(ws, doc["id"]), json={"title": "Runbook", "body": "Drain checkout."}
        )
        assert edited.status_code == 200
    assert _derived(pg, doc["pending_version_id"])["chunks"] == 0
    assert _derived(pg, edited.json()["pending_version_id"])["chunks"] == 1
    assert _receipts(pg, doc["id"]) == [_receipt("replaced", 1, 1)]


async def test_withdrawing_purges_every_version_and_says_bodies_are_retained(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await _upload(owner, ws)
        v1 = doc["pending_version_id"]
        await approve_flagged(owner, ws, doc["id"], v1)
        v2 = (await _upload(owner, ws, document_id=doc["id"]))["pending_version_id"]
        assert (await owner.post(_docs(ws, doc["id"], "/withdraw"))).status_code == 204
    for version in (v1, v2):
        assert _derived(pg, version) == {"derivations": 0, "chunks": 0, "intake_payloads": 0}
    assert _receipts(pg, doc["id"]) == [_receipt("withdrawn", 2, 2, 2)]
    assert sql(pg, "SELECT count(*) FROM document_versions WHERE document_id = %s", doc["id"]) == [
        (2,)
    ]


async def approve_flagged(client, ws, doc_id, version_id):
    review = (await client.get(_docs(ws, doc_id, "/review"))).json()
    approved = await client.post(
        _docs(ws, doc_id, "/approve"),
        json={"version_id": version_id, "acknowledged_receipt": review["receipt"]["sha256"]},
    )
    assert approved.status_code == 200, approved.text


async def test_rejecting_an_upload_purges_its_payload_but_keeps_its_intake(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await _upload(owner, ws)
        version = doc["pending_version_id"]
        ((receipt_sha,),) = sql(
            pg, "SELECT receipt_sha256 FROM intakes WHERE version_id = %s", version
        )
        rejected = await owner.post(_docs(ws, doc["id"], "/reject"), json={"version_id": version})
        assert rejected.status_code == 204
    assert _derived(pg, version) == {"derivations": 0, "chunks": 0, "intake_payloads": 0}
    ((outcome, flags, kept_sha),) = sql(
        pg,
        "SELECT outcome, receipt->'flags', receipt_sha256 FROM intakes WHERE version_id = %s",
        version,
    )
    assert (outcome, kept_sha) == ("accepted", receipt_sha)
    assert [f["kind"] for f in flags] == ["comment"]
    assert _receipts(pg, doc["id"]) == [_receipt("rejected", 1, 1, 1)]


async def test_an_edit_racing_a_withdraw_leaves_nothing_derived(settings, pg):
    app = create_app(settings)
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)
        # Hold the document row so both requests queue on it and run interleaved.
        with psycopg.connect(plain_dsn(pg["app"])) as holder:
            holder.execute("SELECT 1 FROM documents WHERE id = %s FOR UPDATE", (doc["id"],))
            edit = asyncio.create_task(
                owner.put(_docs(ws, doc["id"]), json={"title": "Runbook", "body": "Racing."})
            )
            withdraw = asyncio.create_task(owner.post(_docs(ws, doc["id"], "/withdraw")))
            await asyncio.sleep(0.5)
            holder.rollback()
            edited, withdrawn = await asyncio.wait_for(asyncio.gather(edit, withdraw), 10)
        assert withdrawn.status_code == 204
        assert edited.status_code in (200, 409), edited.text
    for table in DERIVED_TABLES:
        assert sql(pg, f"SELECT 1 FROM {table} WHERE document_id = %s", doc["id"]) == []  # noqa: S608


async def test_a_failed_purge_receipt_leaves_the_document_and_its_chunks(settings, pg, monkeypatch):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)

        def broken_system_event(**fields):
            if fields["channel"] == "system":
                fields["channel"] = "not-a-channel"  # violates a CHECK
            return AuditEvent(**fields)

        monkeypatch.setattr(audit_module, "AuditEvent", broken_system_event)
        with pytest.raises(IntegrityError):
            await owner.post(_docs(ws, doc["id"], "/withdraw"))
    assert sql(pg, "SELECT withdrawn_at FROM documents WHERE id = %s", doc["id"]) == [(None,)]
    assert _derived(pg, doc["pending_version_id"])["chunks"] == 1
    assert _receipts(pg, doc["id"]) == []


async def test_canary_a_withdrawn_upload_leaves_no_derived_row_or_hidden_text(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await _upload(owner, ws)
        await approve_flagged(owner, ws, doc["id"], doc["pending_version_id"])
        found = await owner.get(f"/api/workspaces/{ws}/search", params={"q": "regions"})
        assert [r["document_id"] for r in found.json()["results"]] == [doc["id"]]
        assert (await owner.post(_docs(ws, doc["id"], "/withdraw"))).status_code == 204
        after = await owner.get(f"/api/workspaces/{ws}/search", params={"q": "regions"})
        assert after.json()["results"] == []
    for table in DERIVED_TABLES:
        assert sql(pg, f"SELECT 1 FROM {table} WHERE document_id = %s", doc["id"]) == []  # noqa: S608
    tables = [t for (t,) in sql(pg, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    hex_secret = SECRET.encode().hex()  # bytea renders as hex in a row's text form
    for table in tables:
        leaks = sql(
            pg,
            f"SELECT count(*) FROM {table} x WHERE x::text LIKE %s OR x::text LIKE %s",  # noqa: S608
            f"%{SECRET}%",
            f"%{hex_secret}%",
        )
        assert leaks == [(0,)], table
