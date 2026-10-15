from tests.helpers import add_document, approve, create_workspace, join, signed_in


async def _search(client, ws, q, **params):
    response = await client.get(f"/api/workspaces/{ws}/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()["results"]


async def _shared_group(owner, ws, member) -> str:
    group = (await owner.post(f"/api/workspaces/{ws}/groups", json={"name": "Readers"})).json()
    await owner.put(f"/api/workspaces/{ws}/groups/{group['id']}/members/{member.user_id}")
    return group["id"]


async def _approved_doc(owner, ws, title, body, share_with=None) -> dict:
    doc = await add_document(owner, ws, title=title, body=body)
    await approve(owner, ws, doc)
    if share_with:
        await owner.put(f"/api/workspaces/{ws}/documents/{doc['id']}/shares/{share_with}")
    return doc


async def test_hidden_best_matches_do_not_crowd_out_readable_results(settings):
    """Covers AE10."""
    async with signed_in(settings) as owner, signed_in(settings) as reader:
        ws = await create_workspace(owner)
        await join(owner, ws, reader)
        group = await _shared_group(owner, ws, reader)
        for i in range(10):
            await _approved_doc(
                owner, ws, f"Checkout outage {i}", "checkout checkout checkout checkout failure"
            )
        readable = [
            await _approved_doc(owner, ws, f"Note {i}", "mentions checkout once", group)
            for i in range(6)
        ]
        results = await _search(reader, ws, "checkout", limit=5)
        assert len(results) == 5
        assert {r["document_id"] for r in results} <= {d["id"] for d in readable}


async def test_drafts_never_appear_in_search_for_any_role(settings):
    """Covers AE2."""
    async with signed_in(settings) as owner, signed_in(settings) as author:
        ws = await create_workspace(owner)
        await join(owner, ws, author)
        draft = await add_document(author, ws, title="Payments runbook", body="refund procedure")
        for client in (owner, author):
            assert await _search(client, ws, "refund") == []
        assert draft["pending_version_id"]


async def test_an_admin_finds_an_approved_document_shared_with_no_group(settings):
    """Covers AE1."""
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as admin,
        signed_in(settings) as member,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        await join(owner, ws, member)
        doc = await add_document(member, ws, title="Unshared", body="kafka partition rebalancing")
        assert await _search(admin, ws, "kafka") == []  # still a draft
        await approve(admin, ws, doc)
        assert [r["document_id"] for r in await _search(admin, ws, "kafka")] == [doc["id"]]


async def test_results_never_exceed_the_readable_matches_and_limit_is_capped(settings):
    async with signed_in(settings) as owner, signed_in(settings) as reader:
        ws = await create_workspace(owner)
        await join(owner, ws, reader)
        group = await _shared_group(owner, ws, reader)
        for i in range(3):
            await _approved_doc(owner, ws, f"Shared {i}", "redis eviction policy", group)
        for i in range(25):
            await _approved_doc(owner, ws, f"Hidden {i}", "redis eviction policy")
        assert len(await _search(reader, ws, "redis", limit=20)) == 3
        assert len(await _search(owner, ws, "redis", limit=50)) == 20


async def test_query_syntax_is_treated_as_text(settings):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        await _approved_doc(owner, ws, "DNS", "dns resolution")
        for q in ("dns & | !(", "'; DROP TABLE documents; --", "<script>", ""):
            await _search(owner, ws, q)


async def test_removing_a_member_from_a_group_hides_its_documents_on_the_next_search(settings):
    """Covers AE5."""
    async with signed_in(settings) as owner, signed_in(settings) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        group = await _shared_group(owner, ws, member)
        doc = await _approved_doc(owner, ws, "Platform runbook", "terraform state lock", group)
        own = await add_document(member, ws, title="Mine", body="terraform modules")
        await approve(owner, ws, own)
        assert {r["document_id"] for r in await _search(member, ws, "terraform")} == {
            doc["id"],
            own["id"],
        }
        await owner.delete(f"/api/workspaces/{ws}/groups/{group}/members/{member.user_id}")
        assert [r["document_id"] for r in await _search(member, ws, "terraform")] == [own["id"]]


async def test_withdrawn_documents_vanish_but_earlier_retrievals_stay_audited(settings):
    """Covers AE9."""
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        doc = await _approved_doc(owner, ws, "Old runbook", "elasticsearch reindex")
        first = await _search(owner, ws, "elasticsearch")
        assert [r["document_id"] for r in first] == [doc["id"]]
        await owner.post(f"/api/workspaces/{ws}/documents/{doc['id']}/withdraw")
        assert await _search(owner, ws, "elasticsearch") == []
        events = (
            await owner.get(f"/api/workspaces/{ws}/audit", params={"action": "search"})
        ).json()["events"]
        returned = [set(e["returned_version_ids"]) for e in events]
        assert {doc["pending_version_id"]} in returned


async def test_the_search_audit_row_lists_exactly_the_returned_versions(settings):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        for i in range(3):
            await _approved_doc(owner, ws, f"Doc {i}", "grafana dashboard")
        results = await _search(owner, ws, "grafana")
        latest = (
            await owner.get(f"/api/workspaces/{ws}/audit", params={"action": "search"})
        ).json()["events"][0]
        assert set(latest["returned_version_ids"]) == {r["version_id"] for r in results}
        assert latest["detail"]["query"] == "grafana"
        assert all("[[" in r["snippet"] or r["snippet"] for r in results)
