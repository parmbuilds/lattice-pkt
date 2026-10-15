"""A small, fully known workspace for the endpoint matrix, plus the ground-truth readers.

People and their sessions are inserted with SQL (no Argon2 per case); everything a policy or
trigger guards (workspace, groups, documents, approvals, shares, invites) goes through the API,
so the world is built by the same code paths it later tests.
"""

import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
from pwdlib import PasswordHash

from app import security
from app.auth import cookie_name
from app.config import Settings
from tests.conftest import plain_dsn
from tests.helpers import PASSWORD

# Workspace members by their part in the world, then two people outside it.
MEMBERS = {
    "owner": "owner",
    "admin": "admin",
    "author": "member",
    "reader": "member",
    "other": "member",
}
ACTORS = [*MEMBERS, "outsider", "anon"]
_HASH = PasswordHash.recommended().hash(PASSWORD)  # once per test session, not per case

# Tenant tables in the snapshot; sessions are excluded (every request touches last_seen_at).
TENANT_TABLES = (
    "memberships",
    "groups",
    "group_members",
    "documents",
    "document_versions",
    "version_decisions",
    "document_grants",
    "invites",
    "access_tokens",
    "intakes",
    "intake_payloads",
    "derivations",
    "chunks",
    "entities",
    "entity_aliases",
)


@dataclass
class World:
    ws: str
    users: dict[str, str]  # actor -> user id
    clients: dict[str, httpx.AsyncClient]  # actor -> client ("anon" has no session)
    groups: dict[str, str]  # G: author + reader; H: other
    docs: dict[str, dict[str, str]]  # name -> {"id", "v1", "v2", ...}
    invite_id: str = ""
    invite_token: str = ""
    tokens: dict[str, str] = field(default_factory=dict)  # actor -> own access token id
    token_values: dict[str, str] = field(default_factory=dict)  # actor -> that token's value

    def url(self, tail: str = "") -> str:
        return f"/api/workspaces/{self.ws}{tail}"

    def doc(self, name: str, tail: str = "") -> str:
        return self.url(f"/documents/{self.docs[name]['id']}{tail}")


def _user(conn, settings: Settings, actor: str) -> tuple[str, dict[str, str], str]:
    """Insert a person and a live session; return (user id, cookies, csrf token)."""
    email = f"{actor}-{uuid.uuid4().hex[:10]}@matrix.test"
    uid = conn.execute(
        "INSERT INTO users (email, display_name, password_hash) VALUES (%s, %s, %s) RETURNING id",
        (email, actor.capitalize(), _HASH),
    ).fetchone()[0]
    issued = security.issue("ses", settings.secret_pepper)
    csrf = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (user_id, prefix, verifier, csrf_token, expires_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (uid, issued.prefix, issued.verifier, csrf, datetime.now(UTC) + timedelta(days=1)),
    )
    return str(uid), {cookie_name(settings): issued.value}, csrf


async def _ok(response: httpx.Response) -> dict:
    assert response.is_success, (response.request.method, response.request.url, response.text)
    return response.json() if response.content else {}


@asynccontextmanager
async def build_world(settings: Settings, pg: dict[str, str], app) -> AsyncIterator[World]:
    async with AsyncExitStack() as stack:
        users, clients = {}, {}
        with psycopg.connect(plain_dsn(pg["superuser"]), autocommit=True) as conn:
            for actor in ACTORS:
                headers = {"Origin": settings.public_origin}
                cookies = {}
                if actor != "anon":
                    users[actor], cookies, headers["X-CSRF-Token"] = _user(conn, settings, actor)
                clients[actor] = await stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url=settings.public_origin,
                        headers=headers,
                        cookies=cookies,
                    )
                )
        o, author, other = clients["owner"], clients["author"], clients["other"]
        ws = (await _ok(await o.post("/api/workspaces", json={"name": "Matrix"})))["id"]
        world = World(ws, users, clients, {}, {})
        with psycopg.connect(plain_dsn(pg["superuser"]), autocommit=True) as conn:
            for actor, role in MEMBERS.items():
                if role != "owner":
                    conn.execute(
                        "INSERT INTO memberships (workspace_id, user_id, role) VALUES (%s, %s, %s)",
                        (ws, users[actor], role),
                    )
        for name, people in (("G", ("author", "reader")), ("H", ("other",))):
            group = await _ok(await o.post(world.url("/groups"), json={"name": name}))
            world.groups[name] = group["id"]
            for person in people:
                await _ok(await o.put(world.url(f"/groups/{group['id']}/members/{users[person]}")))

        async def add(client, title, body):
            doc = await _ok(
                await client.post(world.url("/documents"), json={"title": title, "body": body})
            )
            return {"id": doc["id"], "v1": doc["pending_version_id"]}

        async def approve(name, version):
            await _ok(await o.post(world.doc(name, "/approve"), json={"version_id": version}))

        # shared: approved v1 shared with G, plus a pending edit v2 by its author.
        world.docs["shared"] = await add(author, "Checkout runbook", "Restart the checkout pods.")
        await approve("shared", world.docs["shared"]["v1"])
        await _ok(await author.put(world.doc("shared", f"/shares/{world.groups['G']}")))
        edit = await author.put(
            world.doc("shared"), json={"title": "Checkout runbook v2", "body": "Drain checkout."}
        )
        world.docs["shared"]["v2"] = (await _ok(edit))["pending_version_id"]
        # private: approved, shared with no group, written by "other".
        world.docs["private"] = await add(other, "Payments ledger", "Checkout payments balance.")
        await approve("private", world.docs["private"]["v1"])
        # draft: a first draft by the author, never approved.
        world.docs["draft"] = await add(author, "Draft notes", "Checkout scaling ideas.")

        invite = await _ok(await o.post(world.url("/invites"), json={"role": "member"}))
        world.invite_id, world.invite_token = invite["id"], invite["invite_url"].split("#", 1)[1]
        with psycopg.connect(plain_dsn(pg["superuser"]), autocommit=True) as conn:
            for actor in MEMBERS:  # tokens via SQL: creating one through the API costs Argon2
                issued = security.issue("pat", settings.secret_pepper)
                world.token_values[actor] = issued.value
                world.tokens[actor] = str(
                    conn.execute(
                        "INSERT INTO access_tokens (workspace_id, user_id, name, prefix, verifier,"
                        " expires_at) VALUES (%s, %s, 'agent', %s, %s, %s) RETURNING id",
                        (
                            ws,
                            users[actor],
                            issued.prefix,
                            issued.verifier,
                            datetime.now(UTC) + timedelta(days=30),
                        ),
                    ).fetchone()[0]
                )
        yield world


# --- Ground truth ----------------------------------------------------------------------------


def _rows(conn, table: str, workspaces: list[str]) -> set[tuple]:
    cursor = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ANY(%s::uuid[])",  # noqa: S608
        (workspaces,),
    )
    names = [c.name for c in cursor.description]
    return {
        tuple(sorted((n, _plain(v)) for n, v in zip(names, row, strict=True))) for row in cursor
    }


def _plain(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | memoryview):
        return bytes(value).hex()
    if isinstance(value, list):  # JSON lists may hold dicts, so convert their items too
        return tuple(_plain(v) for v in value)
    if isinstance(value, dict):
        return repr(sorted(value.items()))
    return value  # a vector column arrives as its text form: plain psycopg has no adapter


def snapshot(pg, world: World) -> dict[str, set[tuple]]:
    """Every tenant row of the world's workspace, and of any workspace its people belong to."""
    with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
        workspaces = [
            str(w)
            for (w,) in conn.execute(
                "SELECT DISTINCT workspace_id FROM memberships WHERE user_id = ANY(%s::uuid[])",
                (list(world.users.values()),),
            )
        ] + [world.ws]
        snap = {t: _rows(conn, t, workspaces) for t in TENANT_TABLES}
        snap["workspaces"] = {
            tuple(sorted({"id": str(i), "name": n}.items()))
            for i, n in conn.execute(
                "SELECT id, name FROM workspaces WHERE id = ANY(%s::uuid[])", (workspaces,)
            )
        }
    return snap


@dataclass
class Changes:
    added: dict[str, list[dict]]
    removed: dict[str, list[dict]]

    def tables(self) -> set[str]:
        return {t for t in self.added if self.added[t] or self.removed[t]}

    def updated(self, table: str, key: str = "id") -> list[tuple[dict, dict]]:
        """Rows present before and after with the same key: (old, new) pairs."""
        old = {r[key]: r for r in self.removed.get(table, [])}
        return [(old[r[key]], r) for r in self.added.get(table, []) if r[key] in old]


def diff(before: dict[str, set[tuple]], after: dict[str, set[tuple]]) -> Changes:
    added = {t: [dict(r) for r in after[t] - before.get(t, set())] for t in after}
    removed = {t: [dict(r) for r in before.get(t, set()) - after[t]] for t in after}
    return Changes(added, removed)


def audit_rows(pg, workspace_id: str, after_id: int) -> list[dict]:
    with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
        cursor = conn.execute(
            "SELECT * FROM audit_events WHERE workspace_id = %s AND id > %s ORDER BY id",
            (workspace_id, after_id),
        )
        names = [c.name for c in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor]


def last_audit_id(pg) -> int:
    with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
        return conn.execute("SELECT coalesce(max(id), 0) FROM audit_events").fetchone()[0]


def sql(pg, query: str, *params) -> list[tuple]:
    with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
        return conn.execute(query, params).fetchall()
