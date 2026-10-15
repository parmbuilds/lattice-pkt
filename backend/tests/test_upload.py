"""Uploads (U4): the gate before parsing, authorization before extraction, dedupe, intakes,
receipts in review, and acknowledgement on approve (R1-R9, KTD11, KTD13)."""

import asyncio
import hashlib
import random
import string

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

import app.audit as audit_module
from app.ingest import runner
from app.main import create_app
from app.models import AuditEvent
from tests.endpoint_grounds import NOT_FOUND
from tests.endpoint_world import sql
from tests.helpers import (
    PASSWORD,
    add_document,
    api_client,
    approve,
    create_workspace,
    join,
    signed_in,
)

CLEAN_MD = b"# Rota\n\nWho is on call this week.\n"
# A zero-width space in the text and a comment: both are hidden from the body and flagged.
FLAGGED_MD = b"# Failover\n\nSwitch\xe2\x80\x8b regions.\n<!-- COMMENT-SECRET -->\nDone.\n"


def _url(ws: str, tail: str = "") -> str:
    return f"/api/workspaces/{ws}/documents{tail}"


async def _upload(client: httpx.AsyncClient, ws: str, name: str, data: bytes, **fields):
    form = {key: (None, str(value)) for key, value in fields.items()}
    return await client.post(_url(ws, "/upload"), files={"file": (name, data), **form})


def _multipart(name: str, data: bytes) -> tuple[bytes, str]:
    request = httpx.Request("POST", "http://upload.test", files={"file": (name, data)})
    return request.read(), request.headers["content-type"]


def _count(pg, table: str, ws: str) -> int:
    return sql(pg, f"SELECT count(*) FROM {table} WHERE workspace_id = %s", ws)[0][0]  # noqa: S608


def _stored(pg, ws: str) -> dict[str, int]:
    tables = ("documents", "document_versions", "intakes", "intake_payloads")
    return {table: _count(pg, table, ws) for table in tables}


NOTHING = {"documents": 0, "document_versions": 0, "intakes": 0, "intake_payloads": 0}


async def test_a_clean_markdown_upload_becomes_an_unshared_draft_with_its_intake(settings, pg):
    async with signed_in(settings) as owner, signed_in(settings) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        response = await _upload(member, ws, "rota.md", CLEAN_MD)
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["created"] is True and created["serving_version_id"] is None
        version = created["pending_version_id"]

        ((title, body),) = sql(
            pg, "SELECT title, body FROM document_versions WHERE id = %s", version
        )
        assert (title, body) == ("rota", CLEAN_MD.decode())
        assert _count(pg, "document_grants", ws) == 0  # unshared
        ((outcome, source, actor, receipt, receipt_sha, document_id, detected),) = sql(
            pg,
            "SELECT outcome, source_sha256, actor_id::text, receipt, receipt_sha256, "
            "document_id::text, detected_type FROM intakes WHERE version_id = %s",
            version,
        )
        assert (outcome, source, actor, document_id, detected) == (
            "accepted",
            hashlib.sha256(CLEAN_MD).hexdigest(),
            member.user_id,
            created["id"],
            "md",
        )
        assert receipt["flags"] == [] and len(receipt_sha) == 64
        ((original, hidden),) = sql(
            pg,
            "SELECT original, hidden_excerpts FROM intake_payloads WHERE version_id = %s",
            version,
        )
        assert (bytes(original), hidden) == (CLEAN_MD, [])
        ((action, outcome, channel, actor),) = sql(
            pg,
            "SELECT action, outcome, channel, actor_user_id::text FROM audit_events "
            "WHERE workspace_id = %s AND action = 'create_document'",
            ws,
        )
        assert (action, outcome, channel, actor) == (
            "create_document",
            "allow",
            "ui",
            member.user_id,
        )


async def test_an_outsiders_upload_runs_no_parser_and_stores_nothing(settings, pg, monkeypatch):
    async def no_parser(*_args):
        raise AssertionError("the parser ran before authorization")

    monkeypatch.setattr(runner, "extract_in_child", no_parser)
    async with signed_in(settings) as owner, signed_in(settings) as outsider:
        ws = await create_workspace(owner)
        response = await _upload(outsider, ws, "rota.md", CLEAN_MD)
        assert (response.status_code, response.json()) == (404, NOT_FOUND)
        assert _stored(pg, ws) == NOTHING


async def test_a_request_without_a_session_is_refused_before_its_body_is_read(settings):
    body, content_type = _multipart("rota.md", CLEAN_MD)
    read = False

    async def stream():
        nonlocal read
        read = True
        yield body

    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
    async with api_client(settings) as anonymous:
        response = await anonymous.post(
            _url(ws, "/upload"), content=stream(), headers={"content-type": content_type}
        )
    assert (response.status_code, response.json()) == (401, {"detail": "Sign in required."})
    assert read is False


async def test_an_upload_over_the_byte_limit_is_refused_with_or_without_content_length(
    pg, settings
):
    small = settings.model_copy(update={"upload_max_bytes": 1024})
    body, content_type = _multipart("big.md", b"a" * 2048)

    async def chunked():
        for start in range(0, len(body), 256):
            yield body[start : start + 256]

    async with signed_in(small) as owner:
        ws = await create_workspace(owner)
        declared = await owner.post(
            _url(ws, "/upload"), content=body, headers={"content-type": content_type}
        )
        streamed = await owner.post(
            _url(ws, "/upload"), content=chunked(), headers={"content-type": content_type}
        )
        assert "content-length" in declared.request.headers
        assert "content-length" not in streamed.request.headers
        assert declared.status_code == streamed.status_code == 413
        assert _stored(pg, ws) == NOTHING


async def test_uploads_past_the_inflight_limit_get_429_while_other_calls_succeed(settings):
    one = settings.model_copy(update={"upload_max_inflight": 1})
    body, content_type = _multipart("rota.md", CLEAN_MD)
    started, release = asyncio.Event(), asyncio.Event()

    async def held():
        started.set()
        await release.wait()
        yield body

    async with signed_in(one) as owner:
        ws = await create_workspace(owner)
        first = asyncio.create_task(
            owner.post(_url(ws, "/upload"), content=held(), headers={"content-type": content_type})
        )
        try:
            await asyncio.wait_for(started.wait(), 5)
            assert (await _upload(owner, ws, "other.md", b"Other.\n")).status_code == 429
            assert (await owner.get(_url(ws))).status_code == 200
        finally:
            release.set()
        assert (await first).status_code == 201


async def test_titles_come_from_the_filename_or_the_existing_document(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        created = await _upload(owner, ws, "Runbook — failover\u200b.md", CLEAN_MD)
        assert created.status_code == 201, created.text
        doc = created.json()
        (title,) = sql(
            pg, "SELECT title FROM document_versions WHERE id = %s", doc["pending_version_id"]
        )[0]
        assert title == "Runbook — failover"

        renamed = await _upload(owner, ws, "something-else.md", b"Second.\n", document_id=doc["id"])
        assert renamed.status_code == 201, renamed.text
        (title,) = sql(
            pg,
            "SELECT title FROM document_versions WHERE id = %s",
            renamed.json()["pending_version_id"],
        )[0]
        assert title == "Runbook — failover"

        given = await _upload(owner, ws, "x.md", b"Third.\n", title=" Named\u200b ")
        (title,) = sql(
            pg,
            "SELECT title FROM document_versions WHERE id = %s",
            given.json()["pending_version_id"],
        )[0]
        assert title == "Named"


async def test_an_extraction_past_its_timeout_is_rejected_and_its_child_is_gone(
    settings, pg, monkeypatch
):
    children = []
    spawn = asyncio.create_subprocess_exec

    async def recorded(*args, **kwargs):
        children.append(await spawn(*args, **kwargs))
        return children[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorded)
    quick = settings.model_copy(update={"extract_timeout_seconds": 0.001})
    async with signed_in(quick) as owner:
        ws = await create_workspace(owner)
        response = await _upload(owner, ws, "rota.md", CLEAN_MD)
        assert (response.status_code, response.json()) == (422, {"detail": "timeout"})
        assert [child.returncode is not None for child in children] == [True]
        assert sql(pg, "SELECT outcome, reason FROM intakes WHERE workspace_id = %s", ws) == [
            ("rejected", "timeout")
        ]


async def test_an_unsupported_file_is_refused_and_recorded_without_bytes(settings, pg):
    name = "x" * 300 + ".exe"
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        response = await _upload(owner, ws, name, b"MZ\x90\x00 a program")
        assert (response.status_code, response.json()) == (415, {"detail": "unsupported"})
        ((outcome, reason, filename, receipt, receipt_sha, document_id, source),) = sql(
            pg,
            "SELECT outcome, reason, filename, receipt, receipt_sha256, document_id, "
            "source_sha256 FROM intakes WHERE workspace_id = %s",
            ws,
        )
        assert (outcome, reason, filename, receipt, receipt_sha, document_id) == (
            "rejected",
            "unsupported",
            name[:256],
            None,
            None,
            None,
        )
        assert source == hashlib.sha256(b"MZ\x90\x00 a program").hexdigest()
        assert _stored(pg, ws) | {"intakes": 0} == NOTHING  # no draft and no payload


async def test_a_body_too_diverse_to_index_is_refused_and_recorded(settings, pg):
    rng = random.Random(11)
    words = " ".join("".join(rng.choices(string.ascii_lowercase, k=9)) for _ in range(100_000))
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        response = await _upload(owner, ws, "words.txt", words.encode())
        assert (response.status_code, response.json()) == (422, {"detail": "body_too_diverse"})
        assert sql(pg, "SELECT outcome, reason FROM intakes WHERE workspace_id = %s", ws) == [
            ("rejected", "body_too_diverse")
        ]
        assert _stored(pg, ws) | {"intakes": 0} == NOTHING


async def test_identical_bytes_from_the_same_author_point_to_the_first_document(settings, pg):
    async with signed_in(settings) as owner, signed_in(settings) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        first = (await _upload(owner, ws, "rota.md", CLEAN_MD)).json()
        again = await _upload(owner, ws, "copy.md", CLEAN_MD, new_document="true")
        assert again.status_code == 200
        assert (again.json()["id"], again.json()["created"]) == (first["id"], False)
        assert _stored(pg, ws)["intakes"] == 1

        theirs = await _upload(member, ws, "rota.md", CLEAN_MD)
        assert theirs.status_code == 201 and theirs.json()["id"] != first["id"]
        assert _stored(pg, ws)["documents"] == 2


async def test_a_title_matching_an_own_document_is_offered_as_a_new_version(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws, title="Rota", body="Typed.")
        before = _stored(pg, ws)
        offered = await _upload(owner, ws, "ROTA.md", CLEAN_MD)
        assert offered.status_code == 409
        assert offered.json()["suggest_document_id"] == doc["id"]
        assert _stored(pg, ws) == before

        separate = await _upload(owner, ws, "ROTA.md", CLEAN_MD, new_document="true")
        assert separate.status_code == 201 and separate.json()["id"] != doc["id"]


async def test_a_new_version_upload_follows_the_edit_rules(settings, pg):
    async with signed_in(settings) as owner, signed_in(settings) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        doc = await add_document(owner, ws, title="Rota", body="Typed.")
        await approve(owner, ws, doc)
        before = _stored(pg, ws)
        denied = await _upload(member, ws, "rota.md", CLEAN_MD, document_id=doc["id"])
        assert (denied.status_code, denied.json()) == (404, NOT_FOUND)
        assert _stored(pg, ws) == before

        added = await _upload(owner, ws, "rota.md", CLEAN_MD, document_id=doc["id"])
        assert added.status_code == 201, added.text
        ((serving, pending),) = sql(
            pg,
            "SELECT serving_version_id::text, pending_version_id::text FROM documents "
            "WHERE id = %s",
            doc["id"],
        )
        assert (serving, pending) == (doc["pending_version_id"], added.json()["pending_version_id"])


async def test_a_flagged_upload_needs_its_exact_receipt_acknowledged(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = (await _upload(owner, ws, "failover.md", FLAGGED_MD)).json()
        version = doc["pending_version_id"]
        review = (await owner.get(_url(ws, f"/{doc['id']}/review"))).json()

        assert {(f["kind"], f["count"]) for f in review["receipt"]["flags"]} == {
            ("invisible_chars", 1),
            ("comment", 1),
        }
        excerpts = {(h["kind"], h["text"]) for h in review["receipt"]["hidden_excerpts"]}
        assert excerpts == {("invisible_chars", "\\u200b"), ("comment", " COMMENT-SECRET ")}
        assert "COMMENT-SECRET" not in "\n".join(review["diff"])
        assert (review["intake"]["filename"], review["intake"]["detected_type"]) == (
            "failover.md",
            "md",
        )
        assert review["derivation"] == {"status": "ready", "error": None}
        ((stored_sha,),) = sql(
            pg, "SELECT receipt_sha256 FROM intakes WHERE version_id = %s", version
        )
        assert review["receipt"]["sha256"] == stored_sha

        url = _url(ws, f"/{doc['id']}/approve")
        for ack in (None, "0" * 64):
            refused = await owner.post(
                url, json={"version_id": version, "acknowledged_receipt": ack}
            )
            assert refused.status_code == 409
            assert "receipt" in refused.json()["detail"]
        approved = await owner.post(
            url, json={"version_id": version, "acknowledged_receipt": stored_sha}
        )
        assert approved.status_code == 200, approved.text
        assert sql(
            pg,
            "SELECT acknowledged_receipt_sha256 FROM version_decisions WHERE version_id = %s",
            version,
        ) == [(stored_sha,)]


async def test_a_typed_document_approves_without_an_acknowledgement(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await add_document(owner, ws)
        await approve(owner, ws, doc)
        review_less = sql(
            pg,
            "SELECT acknowledged_receipt_sha256 FROM version_decisions WHERE version_id = %s",
            doc["pending_version_id"],
        )
        assert review_less == [(None,)]


async def test_an_upload_whose_audit_insert_fails_leaves_nothing(settings, pg, monkeypatch):
    app = create_app(settings)
    async with signed_in(settings, app) as owner:
        ws = await create_workspace(owner)

        def broken_event(**fields):
            return AuditEvent(**{**fields, "channel": "not-a-channel"})  # violates a CHECK

        monkeypatch.setattr(audit_module, "AuditEvent", broken_event)
        with pytest.raises(IntegrityError):
            await _upload(owner, ws, "rota.md", CLEAN_MD)
        assert _stored(pg, ws) == NOTHING


async def test_a_personal_access_token_cannot_upload(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        created = await owner.post(
            f"/api/workspaces/{ws}/tokens", json={"name": "agent", "password": PASSWORD}
        )
        token = created.json()["token"]
    async with api_client(settings) as agent:
        agent.headers["Authorization"] = f"Bearer {token}"
        response = await agent.post(_url(ws, "/upload"), files={"file": ("rota.md", CLEAN_MD)})
    assert (response.status_code, response.json()) == (
        401,
        {"detail": "Bearer tokens are for /mcp only."},
    )
    assert _stored(pg, ws) == NOTHING
