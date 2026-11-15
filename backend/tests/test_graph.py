from tests.endpoint_world import sql
from tests.helpers import add_document, approve, create_workspace, join, signed_in


def _url(ws, tail=""):
    return f"/api/workspaces/{ws}/entities{tail}"


async def _doc(owner, ws, title, body, share_with=()) -> dict:
    doc = await add_document(owner, ws, title=title, body=body)
    await approve(owner, ws, doc)
    for group in share_with:
        await owner.put(f"/api/workspaces/{ws}/documents/{doc['id']}/shares/{group}")
    return doc


async def _group(owner, ws, name, member) -> str:
    group = (await owner.post(f"/api/workspaces/{ws}/groups", json={"name": name})).json()
    await owner.put(f"/api/workspaces/{ws}/groups/{group['id']}/members/{member.user_id}")
    return group["id"]


async def _entity(client, ws, name, aliases=()) -> str:
    response = await client.post(_url(ws), json={"name": name, "aliases": list(aliases)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _detail(client, ws, entity_id):
    response = await client.get(_url(ws, f"/{entity_id}"))
    return response.status_code, response.json()


async def _names(client, ws) -> set[str]:
    response = await client.get(_url(ws))
    assert response.status_code == 200, response.text
    return {e["name"] for e in response.json()["entities"]}


async def test_each_person_sees_the_mentions_their_groups_can_read(settings):
    """Seed-like sharing: Mei (Platform) and Sam (Payments) on payments-gateway."""
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as mei,
        signed_in(settings) as sam,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, mei)
        await join(owner, ws, sam)
        platform = await _group(owner, ws, "Platform", mei)
        payments = await _group(owner, ws, "Payments", sam)
        body = "The payments-gateway calls the ledger."
        arch = await _doc(owner, ws, "Architecture", body, (platform, payments))
        runbook = await _doc(
            owner, ws, "Timeouts runbook", "Restart payments-gateway.", (platform,)
        )
        incident = await _doc(
            owner, ws, "Incident report", "payments-gateway fell over", (payments,)
        )
        gateway = await _entity(owner, ws, "payments-gateway")
        seen = {}
        for person, client in (("mei", mei), ("sam", sam)):
            status, detail = await _detail(client, ws, gateway)
            assert status == 200
            seen[person] = {m["document_id"] for m in detail["mentions"]}
        assert seen["mei"] == {arch["id"], runbook["id"]}
        assert seen["sam"] == {arch["id"], incident["id"]}


async def test_unreadable_evidence_hides_entities_aliases_and_co_mentions(settings):
    async with signed_in(settings) as owner, signed_in(settings) as reader:
        ws = await create_workspace(owner)
        await join(owner, ws, reader)
        group = await _group(owner, ws, "Readers", reader)
        await _doc(owner, ws, "Shared", "checkout talks to ledger", (group,))
        await _doc(owner, ws, "Private", "checkout via pay-svc and vault")
        checkout = await _entity(owner, ws, "checkout", ["pay-svc"])
        ledger = await _entity(owner, ws, "ledger")
        vault = await _entity(owner, ws, "vault")

        assert await _names(reader, ws) == {"checkout", "ledger"}
        assert await _names(owner, ws) == {"checkout", "ledger", "vault"}
        status, body = await _detail(reader, ws, vault)
        assert (status, body) == (404, {"detail": "Not found or not permitted."})
        assert (await _detail(reader, ws, "00000000-0000-0000-0000-000000000000"))[0] == 404

        status, detail = await _detail(reader, ws, checkout)
        assert status == 200
        assert detail["aliases"] == []  # pay-svc is only in the private document
        assert [c["id"] for c in detail["co_mentions"]] == [ledger]  # not vault
        status, detail = await _detail(owner, ws, checkout)
        assert detail["aliases"] == ["pay-svc"]
        assert {c["id"] for c in detail["co_mentions"]} == {ledger, vault}


async def test_withdrawing_the_only_mention_makes_the_entity_vanish(settings):
    async with signed_in(settings) as owner, signed_in(settings) as reader:
        ws = await create_workspace(owner)
        await join(owner, ws, reader)
        group = await _group(owner, ws, "Readers", reader)
        doc = await _doc(owner, ws, "Notes", "the billing service", (group,))
        billing = await _entity(owner, ws, "billing")
        assert await _names(reader, ws) == {"billing"}
        withdraw = await owner.post(f"/api/workspaces/{ws}/documents/{doc['id']}/withdraw")
        assert withdraw.status_code < 300, withdraw.text
        assert await _names(reader, ws) == set()
        assert (await _detail(reader, ws, billing))[0] == 404


async def test_only_managers_write_the_registry_and_every_write_is_audited(settings, pg):
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as admin,
        signed_in(settings) as member,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, admin, role="admin")
        await join(owner, ws, member)
        refused = await member.post(_url(ws), json={"name": "checkout"})
        assert refused.status_code == 404
        await _entity(admin, ws, "checkout")
        rows = sql(
            pg,
            "SELECT actor_user_id::text, outcome FROM audit_events "
            "WHERE workspace_id = %s AND action = 'manage_entities' ORDER BY id",
            ws,
        )
        assert rows == [(member.user_id, "deny"), (admin.user_id, "allow")]
        assert sql(pg, "SELECT name FROM entities WHERE workspace_id = %s", ws) == [("checkout",)]


async def test_caps_refuse_oversized_writes(settings):
    settings = settings.model_copy(update={"graph_max_entities": 2})
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        long_alias = await owner.post(_url(ws), json={"name": "x", "aliases": ["a" * 101]})
        assert long_alias.status_code == 422
        too_many = await owner.post(
            _url(ws), json={"name": "x", "aliases": list("abcdefghijklmnopqrstu")}
        )
        assert too_many.status_code == 422
        await _entity(owner, ws, "one")
        await _entity(owner, ws, "two", ["a" * 100])
        third = await owner.post(_url(ws), json={"name": "three"})
        assert third.status_code == 422


async def test_matching_is_literal_case_insensitive_on_word_boundaries(settings):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        body = "Gateway is up. gateways-old is not. axb(c and a.b(c and GATEWAY."
        doc = await _doc(owner, ws, "Ops", body)
        gateway = await _entity(owner, ws, "gateway")
        odd = await _entity(owner, ws, "odd", ["a.b(c"])
        (got,) = (await _detail(owner, ws, gateway))[1]["mentions"]
        assert got["document_id"] == doc["id"]
        [span] = got["spans"]  # both matches are close enough to share one passage
        assert [span["snippet"][s:e] for s, e in span["marks"]] == ["Gateway", "GATEWAY"]
        (got,) = (await _detail(owner, ws, odd))[1]["mentions"]
        [span] = got["spans"]
        assert [span["snippet"][s:e] for s, e in span["marks"]] == ["a.b(c"]


async def test_passages_are_whole_words_one_per_run_and_carry_marks(settings):
    """Two mentions in one sentence give one passage, cut at word edges, matches marked (#9)."""
    body = (
        "Opening notes about the rota and the room booking. " * 3
        + "The payments circuit breaker trips, and the payments circuit breaker resets."
        + " Closing notes about staffing and the quarterly review. " * 4
    )
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        await _doc(owner, ws, "Runbook", body)
        breaker = await _entity(owner, ws, "circuit breaker")
        status, detail = await _detail(owner, ws, breaker)
        assert status == 200
        [span] = detail["mentions"][0]["spans"]
        snippet = span["snippet"]
        assert snippet.count("circuit breaker") == 2
        for start, end in span["marks"]:
            assert snippet[start:end] == "circuit breaker"
        core = snippet.strip("…")
        at = body.find(core)
        assert at >= 0 and (at == 0 or body[at - 1].isspace())
        end = at + len(core)
        assert end == len(body) or body[end].isspace()


async def test_a_name_or_alias_cannot_repeat_another_entitys_name_or_alias(settings):
    """Names and aliases share one namespace per workspace, ignoring case (#21)."""
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        await _entity(owner, ws, "checkout", ["cart"])
        clash = [
            {"name": "ping", "aliases": ["Checkout"]},
            {"name": "pay-gw", "aliases": ["cart"]},
            {"name": "x", "aliases": ["X"]},
        ]
        for body in clash:
            response = await owner.post(_url(ws), json=body)
            assert response.status_code == 409, body
        ping = (await owner.post(_url(ws), json={"name": "ping", "aliases": ["ping-svc"]})).json()
        renamed = await owner.patch(_url(ws, f"/{ping['id']}"), json={"name": "checkout"})
        assert renamed.status_code == 409
        kept = await owner.patch(_url(ws, f"/{ping['id']}"), json={"aliases": ["ping-svc"]})
        assert kept.status_code == 200
