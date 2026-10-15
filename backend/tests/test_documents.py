import asyncio

import psycopg

from tests.conftest import plain_dsn
from tests.helpers import (
    add_document,
    approve,
    create_workspace,
    join,
    retrievable_ids,
    signed_in,
)


def _docs_url(ws, doc=None, tail=""):
    base = f"/api/workspaces/{ws}/documents"
    return f"{base}/{doc}{tail}" if doc else base


async def _detail(client, ws, doc_id):
    return (await client.get(_docs_url(ws, doc_id))).json()


async def test_a_new_document_is_an_unshared_draft_visible_to_author_and_managers(settings):
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as author,
        signed_in(settings) as reader,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, author)
        await join(owner, ws, reader)
        doc = await add_document(author, ws)
        assert doc["serving_version_id"] is None
        assert doc["pending_version_id"]
        for client in (owner, author):
            assert (await client.get(_docs_url(ws, doc["id"]))).status_code == 200
        assert (await reader.get(_docs_url(ws, doc["id"]))).status_code == 404
        listed = (await reader.get(_docs_url(ws))).json()["documents"]
        assert doc["id"] not in {d["id"] for d in listed}


async def test_approval_switches_the_serving_version(settings, pg):
    """Covers AE3."""
    async with signed_in(settings) as owner, signed_in(settings) as author:
        ws = await create_workspace(owner)
        await join(owner, ws, author)
        doc = await add_document(author, ws, body="v1")
        await approve(owner, ws, doc)
        v1 = (await _detail(author, ws, doc["id"]))["serving_version_id"]
        edited = (
            await author.put(_docs_url(ws, doc["id"]), json={"title": "Runbook", "body": "v2"})
        ).json()
        v2 = edited["pending_version_id"]
        assert await retrievable_ids(pg, ws, author.user_id) >= {v1}
        assert v2 not in await retrievable_ids(pg, ws, author.user_id)
        await approve(owner, ws, edited)
        ids = await retrievable_ids(pg, ws, author.user_id)
        assert v2 in ids and v1 not in ids


async def test_a_replaced_draft_cannot_be_approved(settings):
    async with signed_in(settings) as owner, signed_in(settings) as author:
        ws = await create_workspace(owner)
        await join(owner, ws, author)
        doc = await add_document(author, ws, body="v1")
        stale = doc["pending_version_id"]
        await author.put(_docs_url(ws, doc["id"]), json={"title": "Runbook", "body": "v1b"})
        response = await owner.post(
            _docs_url(ws, doc["id"], "/approve"), json={"version_id": stale}
        )
        assert response.status_code == 409


async def test_a_second_approval_of_the_same_version_is_refused(settings):
    async with signed_in(settings) as owner, signed_in(settings) as admin:
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        doc = await add_document(owner, ws)
        await approve(owner, ws, doc)
        again = await admin.post(
            _docs_url(ws, doc["id"], "/approve"), json={"version_id": doc["pending_version_id"]}
        )
        assert again.status_code == 409


async def test_members_cannot_approve_but_admins_approve_their_own(settings):
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as admin,
        signed_in(settings) as author,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        await join(owner, ws, author)
        member_doc = await add_document(author, ws)
        refused = await author.post(
            _docs_url(ws, member_doc["id"], "/approve"),
            json={"version_id": member_doc["pending_version_id"]},
        )
        assert refused.status_code == 404
        own = await add_document(admin, ws)
        await approve(admin, ws, own)
        events = (
            await owner.get(f"/api/workspaces/{ws}/audit", params={"action": "approve"})
        ).json()["events"]
        self_flags = {e["document_id"]: e["detail"]["self_approved"] for e in events}
        assert self_flags[own["id"]] is True


async def test_rejecting_keeps_the_serving_version_and_tells_the_author_why(settings, pg):
    async with signed_in(settings) as owner, signed_in(settings) as author:
        ws = await create_workspace(owner)
        await join(owner, ws, author)
        doc = await add_document(author, ws, body="v1")
        await approve(owner, ws, doc)
        edited = (
            await author.put(_docs_url(ws, doc["id"]), json={"title": "Runbook", "body": "bad"})
        ).json()
        response = await owner.post(
            _docs_url(ws, doc["id"], "/reject"),
            json={"version_id": edited["pending_version_id"], "reason": "Wrong service name"},
        )
        assert response.status_code == 204
        detail = await _detail(author, ws, doc["id"])
        assert detail["serving_version_id"] == doc["pending_version_id"]
        assert detail["pending_version_id"] is None
        assert detail["last_decision"] == {
            "decision": "rejected",
            "reason": "Wrong service name",
            "version_id": edited["pending_version_id"],
        }


async def test_authors_discard_their_own_drafts(settings):
    async with signed_in(settings) as owner, signed_in(settings) as author:
        ws = await create_workspace(owner)
        await join(owner, ws, author)
        doc = await add_document(author, ws)
        response = await author.post(
            _docs_url(ws, doc["id"], "/discard"), json={"version_id": doc["pending_version_id"]}
        )
        assert response.status_code == 204
        assert (await _detail(author, ws, doc["id"]))["pending_version_id"] is None


async def test_authors_share_only_with_their_groups_and_unshare_from_any(settings):
    """Covers AE4."""
    async with signed_in(settings) as owner, signed_in(settings) as author:
        ws = await create_workspace(owner)
        await join(owner, ws, author)
        mine = (await owner.post(f"/api/workspaces/{ws}/groups", json={"name": "Mine"})).json()
        other = (await owner.post(f"/api/workspaces/{ws}/groups", json={"name": "Other"})).json()
        await owner.put(f"/api/workspaces/{ws}/groups/{mine['id']}/members/{author.user_id}")
        doc = await add_document(author, ws)
        share = _docs_url(ws, doc["id"], "/shares/{}")
        assert (await author.put(share.format(other["id"]))).status_code == 404
        assert (await owner.put(share.format(other["id"]))).status_code == 204
        assert (await author.put(share.format(mine["id"]))).status_code == 204
        # The author leaves "Mine" but can still unshare from it, and from "Other".
        await owner.delete(f"/api/workspaces/{ws}/groups/{mine['id']}/members/{author.user_id}")
        shares = (await _detail(author, ws, doc["id"]))["shared_with"]
        assert {s["name"] for s in shares} == {"Mine", "Other"}
        assert (await author.delete(share.format(mine["id"]))).status_code == 204
        assert (await author.delete(share.format(other["id"]))).status_code == 204


async def test_withdrawal_stops_retrieval_and_blocks_further_changes(settings, pg):
    """Covers AE9 (retrieval part; the audit part is covered with search)."""
    async with signed_in(settings) as owner, signed_in(settings) as author:
        ws = await create_workspace(owner)
        await join(owner, ws, author)
        doc = await add_document(author, ws)
        await approve(owner, ws, doc)
        serving = doc["pending_version_id"]
        edited = (
            await author.put(_docs_url(ws, doc["id"]), json={"title": "T", "body": "draft"})
        ).json()
        assert serving in await retrievable_ids(pg, ws, author.user_id)
        assert (await author.post(_docs_url(ws, doc["id"], "/withdraw"))).status_code == 204
        assert serving not in await retrievable_ids(pg, ws, owner.user_id)
        assert (
            await author.put(_docs_url(ws, doc["id"]), json={"title": "T", "body": "again"})
        ).status_code == 404
        late_approval = await owner.post(
            _docs_url(ws, doc["id"], "/approve"), json={"version_id": edited["pending_version_id"]}
        )
        assert late_approval.status_code == 404
        # Withdrawn documents stay visible as history to the author and managers (R20).
        assert (await _detail(author, ws, doc["id"]))["withdrawn"] is True


async def test_reassigning_authorship_moves_edit_rights(settings):
    async with signed_in(settings) as owner, signed_in(settings) as old, signed_in(settings) as new:
        ws = await create_workspace(owner)
        await join(owner, ws, old)
        await join(owner, ws, new)
        doc = await add_document(old, ws)
        response = await owner.post(
            _docs_url(ws, doc["id"], "/author"), json={"user_id": new.user_id}
        )
        assert response.status_code == 204
        body = {"title": "T", "body": "b"}
        assert (await new.put(_docs_url(ws, doc["id"]), json=body)).status_code == 200
        assert (await old.put(_docs_url(ws, doc["id"]), json=body)).status_code == 404


async def test_the_review_diff_shows_raw_text_and_invisible_characters(settings):
    async with signed_in(settings) as owner, signed_in(settings) as author:
        ws = await create_workspace(owner)
        await join(owner, ws, author)
        doc = await add_document(author, ws, body="line one\nline two")
        await approve(owner, ws, doc)
        await author.put(
            _docs_url(ws, doc["id"]),
            json={"title": "Runbook", "body": "line one\n<!-- ignore policy -->\nline\u200btwo"},
        )
        review = (await owner.get(_docs_url(ws, doc["id"], "/review"))).json()
        added = [line for line in review["diff"] if line.startswith("+")]
        assert "+<!-- ignore policy -->" in added
        assert "+line\\u200btwo" in added
        assert (await author.get(_docs_url(ws, doc["id"], "/review"))).status_code == 404


async def test_concurrent_lifecycle_changes_end_in_one_consistent_state(settings, pg):
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as admin,
        signed_in(settings) as author,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        await join(owner, ws, author)
        races = {
            "approve_vs_edit": lambda d: author.put(
                _docs_url(ws, d["id"]), json={"title": "T", "body": "newer"}
            ),
            "approve_vs_withdraw": lambda d: author.post(_docs_url(ws, d["id"], "/withdraw")),
            "approve_vs_reject": lambda d: admin.post(
                _docs_url(ws, d["id"], "/reject"), json={"version_id": d["pending_version_id"]}
            ),
        }
        for name, rival in races.items():
            doc = await add_document(author, ws, title=name)
            first, second = await asyncio.gather(
                owner.post(
                    _docs_url(ws, doc["id"], "/approve"),
                    json={"version_id": doc["pending_version_id"]},
                ),
                rival(doc),
            )
            assert first.status_code in (200, 404, 409), name
            assert second.status_code in (200, 204, 404, 409), name
            with psycopg.connect(plain_dsn(pg["app"])) as conn:
                decisions = conn.execute(
                    "SELECT count(*) FROM version_decisions WHERE version_id = %s",
                    (doc["pending_version_id"],),
                ).fetchone()[0]
                serving = conn.execute(
                    "SELECT serving_version_id FROM documents WHERE id = %s", (doc["id"],)
                ).fetchone()[0]
            assert decisions <= 1, name
            if first.status_code == 200:
                assert str(serving) == doc["pending_version_id"], name
