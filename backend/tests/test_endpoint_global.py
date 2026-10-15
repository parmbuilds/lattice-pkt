"""Endpoints outside a workspace, the MCP endpoint, and the guard that every route is covered.

Each test checks the response against the database and the exact rows the call changed, like
the workspace matrix in test_endpoint_matrix.py.
"""

import json
import uuid
from contextlib import asynccontextmanager

import pytest
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from app.auth import cookie_name
from app.main import create_app
from tests.conftest import live_server
from tests.endpoint_grounds import (
    NOT_FOUND,
    Ctx,
    ground_search_results,
    hmac_of,
    only,
    retrievable,
    unchanged,
)
from tests.endpoint_world import (
    MEMBERS,
    audit_rows,
    build_world,
    diff,
    last_audit_id,
    snapshot,
    sql,
)
from tests.helpers import PASSWORD
from tests.test_endpoint_matrix import MATRIX_ROUTES

GLOBAL_ROUTES: set[tuple[str, str]] = set()


def covers(method: str, path: str):
    """Register the route a test grounds, for the coverage guard at the bottom."""
    GLOBAL_ROUTES.add((method, path))
    return lambda test: test


@pytest.fixture(scope="module")
def app(settings):
    return create_app(settings)


@asynccontextmanager
async def world_call(settings, pg, app):
    """A fresh world plus a `measure` helper: before/after snapshot and new audit rows."""
    async with build_world(settings, pg, app) as world:

        def measure():
            before, audit_from = snapshot(pg, world), last_audit_id(pg)
            return lambda: (diff(before, snapshot(pg, world)), audit_from)

        yield world, measure


def ctx(world, actor, changes, pg, body=None):
    return Ctx(world, actor, body or {}, changes, [], pg)


def email_of(pg, user_id: str) -> str:
    return sql(pg, "SELECT email FROM users WHERE id = %s", user_id)[0][0]


@covers("GET", "/api/health")
async def test_health(settings, pg, app):
    async with world_call(settings, pg, app) as (world, measure):
        done = measure()
        response = await world.clients["anon"].get("/api/health")
        assert (response.status_code, response.json()) == (200, {"status": "ok"})
        unchanged(ctx(world, "anon", done()[0], pg))


@covers("POST", "/api/auth/signup")
async def test_sign_up(settings, pg, app):
    async with world_call(settings, pg, app) as (world, _):
        anon, email = world.clients["anon"], f"new-{uuid.uuid4().hex[:8]}@matrix.test"
        body = {"email": email, "password": PASSWORD, "name": " New Person "}
        response = await anon.post("/api/auth/signup", json=body)
        assert response.status_code == 201
        ((uid, name, password_hash),) = sql(
            pg, "SELECT id::text, display_name, password_hash FROM users WHERE email = %s", email
        )
        assert response.json() == {"id": uid} and name == "New Person"
        assert password_hash.startswith("$argon2id$") and PASSWORD not in password_hash
        assert sql(pg, "SELECT count(*) FROM sessions WHERE user_id = %s", uid) == [(0,)]
        # A taken email or a short password: the same refusal, and no new person.
        for bad in (
            {**body, "email": email.upper()},
            {**body, "email": "x@y.z", "password": "short"},
        ):
            refused = await anon.post("/api/auth/signup", json=bad)
            assert (refused.status_code, refused.json()["detail"]) == (
                400,
                "Could not create an account with these details.",
            )
        assert sql(pg, "SELECT count(*) FROM users WHERE lower(email) IN (%s, 'x@y.z')", email) == [
            (1,)
        ]


@covers("POST", "/api/auth/signin")
async def test_sign_in(settings, pg, app):
    async with world_call(settings, pg, app) as (world, _):
        anon, uid = world.clients["anon"], world.users["reader"]
        email = email_of(pg, uid)
        sessions = "SELECT id, prefix, verifier, csrf_token FROM sessions WHERE user_id = %s"
        before = sql(pg, sessions, uid)
        wrong = await anon.post("/api/auth/signin", json={"email": email, "password": "nope-nope"})
        assert (wrong.status_code, wrong.json()) == (401, {"detail": "Invalid email or password."})
        assert sql(pg, sessions, uid) == before  # a refused sign-in creates no session
        response = await anon.post(
            "/api/auth/signin", json={"email": email.upper(), "password": PASSWORD}
        )
        assert response.status_code == 200
        (new,) = [row for row in sql(pg, sessions, uid) if row not in before]
        cookie = response.cookies[cookie_name(settings)]
        assert response.json() == {"csrf_token": new[3]}
        assert cookie.startswith(f"lat_ses_{new[1]}_") and bytes(new[2]).hex() == hmac_of(cookie)


@covers("POST", "/api/auth/signout")
async def test_sign_out(settings, pg, app):
    async with world_call(settings, pg, app) as (world, _):
        reader, uid = world.clients["reader"], world.users["reader"]
        live = "SELECT count(*) FROM sessions WHERE user_id = %s AND revoked_at IS NULL"
        csrf = reader.headers.pop("X-CSRF-Token")
        assert (await reader.post("/api/auth/signout")).status_code == 403  # CSRF required
        assert sql(pg, live, uid) == [(1,)]
        reader.headers["X-CSRF-Token"] = csrf
        assert (await world.clients["anon"].post("/api/auth/signout")).status_code == 401
        response = await reader.post("/api/auth/signout")
        assert response.status_code == 204 and sql(pg, live, uid) == [(0,)]
        assert (await reader.get("/api/auth/me")).status_code == 401


@covers("GET", "/api/auth/me")
async def test_me(settings, pg, app):
    async with world_call(settings, pg, app) as (world, measure):
        done = measure()
        for actor in ("owner", "reader", "outsider"):
            uid = world.users[actor]
            body = (await world.clients[actor].get("/api/auth/me")).json()
            ((email, name),) = sql(pg, "SELECT email, display_name FROM users WHERE id = %s", uid)
            assert body["user"] == {"id": uid, "email": email, "name": name}
            rows = sql(
                pg,
                "SELECT workspace_id::text, role FROM memberships WHERE user_id = %s",
                uid,
            )
            assert sorted((m["workspace_id"], m["role"]) for m in body["memberships"]) == sorted(
                rows
            )
            assert body["csrf_token"] == world.clients[actor].headers["X-CSRF-Token"]
        anon = await world.clients["anon"].get("/api/auth/me")
        assert (anon.status_code, anon.json()) == (401, {"detail": "Sign in required."})
        unchanged(ctx(world, "anon", done()[0], pg))


@covers("POST", "/api/workspaces")
async def test_create_workspace(settings, pg, app):
    async with world_call(settings, pg, app) as (world, measure):
        for actor, name, status in (("anon", "Nope", 401), ("outsider", "   ", 400)):
            done = measure()
            response = await world.clients[actor].post("/api/workspaces", json={"name": name})
            assert response.status_code == status
            unchanged(ctx(world, actor, done()[0], pg))
        done = measure()
        response = await world.clients["outsider"].post("/api/workspaces", json={"name": " Ops "})
        changes, audit_from = done()
        only(changes, "workspaces", "memberships")
        (ws,), (membership,) = changes.added["workspaces"], changes.added["memberships"]
        assert response.status_code == 201 and response.json() == {"id": ws["id"], "name": "Ops"}
        assert (membership["user_id"], membership["role"]) == (world.users["outsider"], "owner")
        ((row,),) = [audit_rows(pg, ws["id"], audit_from)]
        assert (row["action"], row["outcome"], str(row["actor_user_id"])) == (
            "create_workspace",
            "allow",
            world.users["outsider"],
        )


@covers("POST", "/api/invites/accept")
async def test_accept_invite(settings, pg, app):
    async with world_call(settings, pg, app) as (world, measure):
        token = world.invite_token
        # Unknown token, anonymous caller, and an existing member: refused, nothing changes.
        for actor, sent, status in (
            ("outsider", token[:-4] + "AAAA", 404),
            ("anon", token, 401),
            ("reader", token, 404),
        ):
            done = measure()
            response = await world.clients[actor].post("/api/invites/accept", json={"token": sent})
            assert response.status_code == status
            changes, audit_from = done()
            unchanged(ctx(world, actor, changes, pg))
            audit = audit_rows(pg, world.ws, audit_from)
            if actor == "reader":  # a genuine invite holder's refusal is traceable (R6)
                ((row,),) = [audit]
                assert (row["action"], row["outcome"], row["rule_id"]) == (
                    "accept_invite",
                    "deny",
                    "accept_invite.already_member",
                )
            else:
                assert audit == []
        done = measure()
        response = await world.clients["outsider"].post(
            "/api/invites/accept", json={"token": token}
        )
        assert response.json() == {"workspace_id": world.ws, "role": "member"}
        changes, audit_from = done()
        only(changes, "memberships", "invites")
        (membership,) = changes.added["memberships"]
        assert (membership["user_id"], membership["role"]) == (world.users["outsider"], "member")
        ((old, new),) = changes.updated("invites")
        assert old["accepted_at"] is None and new["accepted_by"] == world.users["outsider"]
        ((row,),) = [audit_rows(pg, world.ws, audit_from)]
        assert (row["action"], row["outcome"]) == ("accept_invite", "allow")


# --- MCP: the agent channel, per person ----------------------------------------------------


@asynccontextmanager
async def agent(base_url: str, token: str):
    transport = streamable_http_client(
        f"{base_url}/mcp",
        http_client=create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}),
    )
    async with Client(transport) as client:
        yield client


async def test_mcp_endpoint_is_grounded_for_every_member(settings, pg, app):
    """search_knowledge and get_evidence return what each person may retrieve, and nothing
    else; each call writes one `mcp` audit row naming the token, and changes no content."""
    with live_server(create_app(settings)) as base_url:
        async with world_call(settings, pg, app) as (world, measure):
            shared_v1, private_v1 = world.docs["shared"]["v1"], world.docs["private"]["v1"]
            for actor in MEMBERS:
                done = measure()
                async with agent(base_url, world.token_values[actor]) as client:
                    tools = await client.list_tools()
                    assert {t.name for t in tools.tools} == {"search_knowledge", "get_evidence"}
                    found = await client.call_tool("search_knowledge", {"query": "checkout"})
                    results = json.loads(found.content[0].text)["results"]
                    got = sorted(r["version_id"] for r in results)
                    assert got == retrievable(world, actor), actor
                    ground_search_results(pg, results)
                    evidence = {}
                    for vid in (shared_v1, private_v1, world.docs["shared"]["v2"]):
                        result = await client.call_tool("get_evidence", {"version_id": vid})
                        evidence[vid] = not result.is_error
                        if not result.is_error:
                            text = json.loads(result.content[0].text)
                            ((title, body),) = sql(
                                pg, "SELECT title, body FROM document_versions WHERE id = %s", vid
                            )
                            assert (text["title"], text["body"]) == (title, body)
                        else:
                            assert NOT_FOUND["detail"] in result.content[0].text
                changes, audit_from = done()
                only(changes, "access_tokens")  # only the token's last-used time moves
                assert {u["id"] for _, u in changes.updated("access_tokens")} == {
                    world.tokens[actor]
                }
                allowed = set(retrievable(world, actor))
                assert evidence == {v: v in allowed for v in evidence}, actor  # never a draft
                audit = audit_rows(pg, world.ws, audit_from)
                assert {(r["channel"], str(r["token_id"])) for r in audit} == {
                    ("mcp", world.tokens[actor])
                }
                (search_row,) = [r for r in audit if r["action"] == "search"]
                assert sorted(str(v) for v in search_row["returned_version_ids"]) == got


# --- Coverage guard ------------------------------------------------------------------------


def test_every_route_has_a_grounded_test(settings):
    """A new endpoint fails this until it is added to the matrix or to this file."""
    paths = create_app(settings).openapi()["paths"]
    routes = {(method.upper(), path) for path, ops in paths.items() for method in ops}
    assert routes == MATRIX_ROUTES | GLOBAL_ROUTES, {
        "untested": sorted(routes - MATRIX_ROUTES - GLOBAL_ROUTES),
        "stale": sorted((MATRIX_ROUTES | GLOBAL_ROUTES) - routes),
    }
