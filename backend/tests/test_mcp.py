import hashlib
import json
from contextlib import asynccontextmanager

import httpx
import psycopg
import pytest
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from pydantic import SecretStr

from app.config import DEV_AI_KEY
from app.main import create_app
from app.mcp_server import DECLARED
from tests.conftest import live_server, plain_dsn
from tests.endpoint_grounds import AI_PROVIDER
from tests.helpers import (
    PASSWORD,
    add_document,
    approve,
    create_workspace,
    join,
    signed_in,
)

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def server(settings):
    with live_server(create_app(settings)) as base_url:
        yield base_url


@asynccontextmanager
async def agent(base_url: str, token: str):
    transport = streamable_http_client(
        f"{base_url}/mcp",
        http_client=create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}),
    )
    async with Client(transport) as client:
        yield client


async def _token(client, ws) -> str:
    response = await client.post(
        f"/api/workspaces/{ws}/tokens", json={"name": "agent", "password": PASSWORD}
    )
    response.raise_for_status()
    return response.json()["token"]


def _payload(result) -> dict:
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


async def _agent_search(base_url, token, query, **extra) -> set[str]:
    async with agent(base_url, token) as client:
        result = await client.call_tool("search_knowledge", {"query": query, **extra})
    return {r["version_id"] for r in _payload(result)["results"]}


async def _raw_initialize(base_url, headers=None) -> httpx.Response:
    async with httpx.AsyncClient() as http:
        return await http.post(
            f"{base_url}/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **(headers or {})}
        )


async def test_the_tool_list_is_exactly_the_two_retrieval_tools(settings, server):
    """Covers AE11."""
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        async with agent(server, await _token(owner, ws)) as client:
            tools = await client.list_tools()
    assert sorted(t.name for t in tools.tools) == ["get_evidence", "search_knowledge"]
    # Anything outside DECLARED is audited as an override, so the two must never drift (R20).
    assert {t.name: set(t.input_schema["properties"]) for t in tools.tools} == DECLARED


async def test_missing_malformed_revoked_and_expired_tokens_get_401(settings, server, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        revoked = await _token(owner, ws)
        expired = await _token(owner, ws)
        tokens = (await owner.get(f"/api/workspaces/{ws}/tokens")).json()["tokens"]
        by_prefix = {t["prefix"]: t["id"] for t in tokens}
        await owner.delete(f"/api/workspaces/{ws}/tokens/{by_prefix[revoked.split('_')[2]]}")
        with psycopg.connect(plain_dsn(pg["app"])) as conn:
            conn.execute(
                "UPDATE access_tokens SET expires_at = now() - interval '1 second' "
                "WHERE prefix = %s",
                (expired.split("_")[2],),
            )
    for headers in (
        None,
        {"Authorization": "Bearer not-a-lattice-token"},
        {"Authorization": f"Bearer {revoked}"},
        {"Authorization": f"Bearer {expired}"},
        {"Authorization": f"Bearer {revoked[:-4]}abcd"},
    ):
        assert (await _raw_initialize(server, headers)).status_code == 401, headers


async def test_a_session_cookie_is_not_accepted_on_mcp(settings, server):
    async with signed_in(settings) as owner:
        await create_workspace(owner)
        cookies = "; ".join(f"{k}={v}" for k, v in owner.cookies.items())
    assert (await _raw_initialize(server, {"Cookie": cookies})).status_code == 401


async def test_a_foreign_host_header_is_refused(settings, server):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        token = await _token(owner, ws)
    response = await _raw_initialize(
        server, {"Authorization": f"Bearer {token}", "Host": "evil.example"}
    )
    assert response.status_code in (400, 403, 421)


async def test_a_removed_members_token_is_refused_on_its_next_use(settings, server):
    async with signed_in(settings) as owner, signed_in(settings) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        token = await _token(member, ws)
        assert await _agent_search(server, token, "anything") == set()
        await owner.delete(f"/api/workspaces/{ws}/members/{member.user_id}")
    assert (await _raw_initialize(server, {"Authorization": f"Bearer {token}"})).status_code == 401


async def test_an_agent_sees_only_its_token_workspace_member_view(settings, server):
    """Covers AE6."""
    async with (
        signed_in(settings) as owner_a,
        signed_in(settings) as owner_b,
        signed_in(settings) as person,
    ):
        ws_a = await create_workspace(owner_a, "A")
        ws_b = await create_workspace(owner_b, "B")
        await join(owner_a, ws_a, person, "member")
        await join(owner_b, ws_b, person, "admin")
        hidden_in_a = await add_document(owner_a, ws_a, "A runbook", "vault unseal procedure")
        await approve(owner_a, ws_a, hidden_in_a)
        in_b = await add_document(owner_b, ws_b, "B runbook", "vault unseal procedure")
        await approve(owner_b, ws_b, in_b)
        token_a = await _token(person, ws_a)
        token_b = await _token(person, ws_b)
    assert await _agent_search(server, token_a, "vault") == set()
    assert await _agent_search(server, token_b, "vault") == {in_b["pending_version_id"]}


async def test_injected_identity_arguments_are_ignored_and_audited(settings, server):
    """Covers AE7."""
    async with signed_in(settings) as owner, signed_in(settings) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        secret = await add_document(owner, ws, "Secret", "payroll export schedule")
        await approve(owner, ws, secret)
        token = await _token(member, ws)
        spoofed = await _agent_search(
            server, token, "payroll", user_id=owner.user_id, group="admins", role="owner"
        )
        assert spoofed == set()
        events = (
            await owner.get(
                f"/api/workspaces/{ws}/audit",
                params={"action": "search", "user_id": member.user_id},
            )
        ).json()["events"]
    assert events[0]["channel"] == "mcp"
    assert events[0]["token_id"]
    assert events[0]["detail"]["attempted_overrides"] == {
        "user_id": owner.user_id,
        "group": "admins",
        "role": "owner",
    }


async def test_get_evidence_returns_only_serving_versions(settings, server):
    async with signed_in(settings) as owner, signed_in(settings) as other_owner:
        ws = await create_workspace(owner)
        elsewhere_ws = await create_workspace(other_owner)
        doc = await add_document(owner, ws, "Runbook", "v1 text")
        await approve(owner, ws, doc)
        superseded = doc["pending_version_id"]
        edited = (
            await owner.put(
                f"/api/workspaces/{ws}/documents/{doc['id']}",
                json={"title": "Runbook", "body": "v2 text"},
            )
        ).json()
        await approve(owner, ws, edited)
        draft = (
            await owner.put(
                f"/api/workspaces/{ws}/documents/{doc['id']}",
                json={"title": "Runbook", "body": "v3 draft"},
            )
        ).json()["pending_version_id"]
        elsewhere = await add_document(other_owner, elsewhere_ws, "X", "y")
        await approve(other_owner, elsewhere_ws, elsewhere)
        token = await _token(owner, ws)

    async with agent(server, token) as client:
        ok = await client.call_tool("get_evidence", {"version_id": edited["pending_version_id"]})
        refusals = [
            await client.call_tool("get_evidence", {"version_id": vid})
            for vid in (
                superseded,
                draft,
                elsewhere["pending_version_id"],
                "00000000-0000-0000-0000-000000000000",
                "not-a-uuid",
            )
        ]
    evidence = _payload(ok)
    assert evidence["body"] == "v2 text"
    assert evidence["kind"] == "retrieved_data"
    assert evidence["approved_by"]
    assert all(r.is_error for r in refusals)
    assert len({r.content[0].text for r in refusals}) == 1  # one indistinguishable response


async def test_mcp_and_ui_search_return_the_same_versions(settings, server):
    async with signed_in(settings) as owner, signed_in(settings) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        group = (await owner.post(f"/api/workspaces/{ws}/groups", json={"name": "G"})).json()
        await owner.put(f"/api/workspaces/{ws}/groups/{group['id']}/members/{member.user_id}")
        for i in range(4):
            doc = await add_document(owner, ws, f"Doc {i}", "nginx upstream timeout")
            await approve(owner, ws, doc)
            if i % 2:
                await owner.put(f"/api/workspaces/{ws}/documents/{doc['id']}/shares/{group['id']}")
        ui = (await member.get(f"/api/workspaces/{ws}/search", params={"q": "nginx"})).json()[
            "results"
        ]
        token = await _token(member, ws)
    assert await _agent_search(server, token, "nginx") == {r["version_id"] for r in ui}
    assert len(ui) == 2


async def test_agents_get_the_same_results_and_tools_with_a_provider_connected(settings):
    """Covers AE7: retrieval results only, never a generated answer (R24)."""
    connected = settings.model_copy(
        update={"ai_providers": [AI_PROVIDER], "ai_key_encryption_keys": [SecretStr(DEV_AI_KEY)]}
    )

    async def results(base_url, token) -> dict:
        async with agent(base_url, token) as client:
            return _payload(await client.call_tool("search_knowledge", {"query": "nginx"}))

    with live_server(create_app(connected)) as base_url:
        async with signed_in(connected) as owner:
            ws = await create_workspace(owner)
            await approve(owner, ws, await add_document(owner, ws, "Proxy", "nginx upstream"))
            token = await _token(owner, ws)
            before = await results(base_url, token)
            saved = await owner.put(
                f"/api/workspaces/{ws}/ai/settings",
                json={
                    "provider_id": "fake",
                    "model": "fake-1",
                    "retention_days": 30,
                    "api_key": "k",
                },
            )
            assert saved.json()["enabled"]
            after = await results(base_url, token)
            async with agent(base_url, token) as client:
                tools = await client.list_tools()
    assert after == before and len(before["results"]) == 1
    assert set(after) == {"note", "results"}
    assert sorted(t.name for t in tools.tools) == ["get_evidence", "search_knowledge"]


async def test_a_demoted_admins_agent_keeps_working_with_member_access(settings, server):
    """Covers AE8."""
    async with signed_in(settings) as owner, signed_in(settings) as admin:
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        doc = await add_document(owner, ws, "Unshared", "consul service mesh")
        await approve(owner, ws, doc)
        token = await _token(admin, ws)
        assert await _agent_search(server, token, "consul") == {doc["pending_version_id"]}
        await owner.patch(f"/api/workspaces/{ws}/members/{admin.user_id}", json={"role": "member"})
    assert await _agent_search(server, token, "consul") == set()


async def test_get_evidence_returns_exactly_the_requested_span(settings, server):
    body = "Restart the checkout pods.\n\nThen drain the queue."
    async with signed_in(settings) as owner, signed_in(settings) as other_owner:
        ws = await create_workspace(owner)
        elsewhere_ws = await create_workspace(other_owner)
        doc = await add_document(owner, ws, "Runbook", "v1 checkout text")
        await approve(owner, ws, doc)
        superseded = doc["pending_version_id"]
        edited = (
            await owner.put(
                f"/api/workspaces/{ws}/documents/{doc['id']}",
                json={"title": "Runbook", "body": body},
            )
        ).json()
        await approve(owner, ws, edited)
        serving = edited["pending_version_id"]
        elsewhere = await add_document(other_owner, elsewhere_ws, "X", "checkout elsewhere")
        await approve(other_owner, elsewhere_ws, elsewhere)
        token = await _token(owner, ws)

        async with agent(server, token) as client:
            found = _payload(await client.call_tool("search_knowledge", {"query": "checkout"}))
            (result,) = found["results"]
            (span,) = result["spans"]
            ok = await client.call_tool(
                "get_evidence", {"version_id": serving, "start": 8, "end": 20}
            )
            refusals = [
                await client.call_tool("get_evidence", {"version_id": vid, **bounds})
                for vid, bounds in (
                    (serving, {"start": 0, "end": len(body) + 1}),
                    (serving, {"start": 5, "end": 5}),
                    (serving, {"start": -1, "end": 3}),
                    (serving, {"start": 0}),
                    (superseded, {"start": 0, "end": 3}),
                    (elsewhere["pending_version_id"], {"start": 0, "end": 3}),
                    ("not-a-uuid", {"start": 0, "end": 3}),
                    (superseded, {}),  # the refusal every unreadable id already gets
                )
            ]
            await client.call_tool(
                "get_evidence", {"version_id": serving, "start": 0, "end": 7, "page": 2}
            )
        events = (
            await owner.get(f"/api/workspaces/{ws}/audit", params={"action": "retrieve"})
        ).json()["events"]
    assert (result["version_id"], result["retrieval"]) == (serving, "hybrid")
    assert span["sha256"] == hashlib.sha256(body[span["start"] : span["end"]].encode()).hexdigest()
    evidence = _payload(ok)
    assert evidence["body"] == "the checkout" == body[8:20]
    assert evidence["span"] == {
        "start": 8,
        "end": 20,
        "sha256": hashlib.sha256(b"the checkout").hexdigest(),
    }
    assert all(r.is_error for r in refusals)
    assert len({r.content[0].text for r in refusals}) == 1  # one indistinguishable response
    with_unknown, with_span = [e for e in events if e["returned_version_ids"]]
    # start and end are declared arguments; only the unknown one is an attempted override.
    assert with_span["detail"]["returned_spans"] == [[serving, 8, 20]]
    assert "attempted_overrides" not in with_span["detail"]
    assert with_unknown["detail"]["returned_spans"] == [[serving, 0, 7]]
    assert with_unknown["detail"]["attempted_overrides"] == {"page": 2}


async def test_search_history_lists_your_own_searches_and_your_agents_newest_first(
    settings, server
):
    async with signed_in(settings) as owner, signed_in(settings) as reader:
        ws = await create_workspace(owner)
        await join(owner, ws, reader)
        doc = await add_document(owner, ws, title="Checkout runbook", body="checkout timeouts")
        await approve(owner, ws, doc)
        search = f"/api/workspaces/{ws}/search"
        found = (await owner.get(search, params={"q": "checkout"})).json()["results"]
        await owner.get(search, params={"q": "timeouts", "via": "ask"})
        await _agent_search(server, await _token(owner, ws), "checkout agent")
        await reader.get(search, params={"q": "only the reader asked this"})

        history = (await owner.get(f"{search}/history")).json()["searches"]
        assert [(s["source"], s["query"], s["token_name"]) for s in history] == [
            ("agent", "checkout agent", "agent"),
            ("ask", "timeouts", None),
            ("search", "checkout", None),
        ]
        assert history[2]["count"] == len(found)
        for i in range(10):
            await owner.get(search, params={"q": f"query {i}"})
        latest = (await owner.get(f"{search}/history")).json()["searches"]
        assert [s["query"] for s in latest] == [f"query {i}" for i in range(9, -1, -1)]
