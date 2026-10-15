"""Grounding checks for the endpoint matrix: each compares a response with the database.

Expected visibility (VISIBLE, RETRIEVABLE) is written out from the plan's rules for the known
world in tests/endpoint_world.py, not derived from app.policy, so it stays an independent oracle.
"""

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from tests.endpoint_world import MEMBERS, Changes, World, sql

MANAGERS = {"owner", "admin"}
EVERY_MEMBER = set(MEMBERS)
NOT_FOUND = {"detail": "Not found or not permitted."}


@dataclass
class Ctx:
    world: World
    actor: str
    body: dict
    changes: Changes
    audit: list[dict]
    pg: dict

    def id(self, actor: str) -> str:
        return self.world.users[actor]


def only(changes: Changes, *tables: str) -> None:
    assert changes.tables() == set(tables), changes.tables()


def unchanged(ctx: Ctx) -> None:
    only(ctx.changes)


def updated_one(ctx: Ctx, table: str, key: str = "id") -> tuple[dict, dict]:
    pairs = ctx.changes.updated(table, key)
    assert len(pairs) == 1 and len(ctx.changes.added[table]) == 1, ctx.changes
    return pairs[0]


def recent(ts: datetime, delta: timedelta = timedelta()) -> bool:
    return abs(ts - (datetime.now(UTC) + delta)) < timedelta(minutes=1)


def hmac_of(value: str) -> str:
    return hmac.new(b"test-pepper-0123456789", value.encode(), sha256).hexdigest()


def doc_row(ctx: Ctx, version_id: str) -> tuple[str, str]:
    ((title, body),) = sql(
        ctx.pg, "SELECT title, body FROM document_versions WHERE id = %s", version_id
    )
    return title, body


# --- Members, invites, ownership -------------------------------------------------------------


def g_members(ctx: Ctx) -> None:
    unchanged(ctx)
    rows = sql(
        ctx.pg,
        "SELECT u.id::text, u.display_name, u.email, m.role FROM memberships m "
        "JOIN users u ON u.id = m.user_id WHERE m.workspace_id = %s",
        ctx.world.ws,
    )
    got = [(m["user_id"], m["name"], m["email"], m["role"]) for m in ctx.body["members"]]
    assert sorted(got) == sorted(rows) and len(got) == 5
    assert [m[1] for m in got] == sorted(m[1] for m in got)  # ordered by name


def g_role(ctx: Ctx) -> None:
    only(ctx.changes, "memberships")
    old, new = updated_one(ctx, "memberships", key="user_id")
    assert (old["user_id"], old["role"], new["role"]) == (ctx.id("other"), "member", "admin")
    assert ctx.body == {"user_id": ctx.id("other"), "role": "admin"}


def g_remove_member(ctx: Ctx) -> None:
    only(ctx.changes, "memberships", "group_members", "access_tokens")
    assert [r["user_id"] for r in ctx.changes.removed["memberships"]] == [ctx.id("other")]
    assert ctx.changes.added["memberships"] == []
    assert [(r["user_id"], r["group_id"]) for r in ctx.changes.removed["group_members"]] == [
        (ctx.id("other"), ctx.world.groups["H"])
    ]
    old, new = updated_one(ctx, "access_tokens")
    assert old["id"] == ctx.world.tokens["other"] and old["revoked_at"] is None
    assert recent(new["revoked_at"])


def g_transfer(ctx: Ctx) -> None:
    only(ctx.changes, "memberships")
    roles = {
        new["user_id"]: (old["role"], new["role"])
        for old, new in ctx.changes.updated("memberships", "user_id")
    }
    assert roles == {ctx.id("owner"): ("owner", "admin"), ctx.id("admin"): ("admin", "owner")}
    assert ctx.body == {"owner_id": ctx.id("admin")}


def g_new_invite(ctx: Ctx) -> None:
    only(ctx.changes, "invites")
    (row,) = ctx.changes.added["invites"]
    secret = ctx.body["invite_url"].split("#", 1)[1]
    assert (row["id"], row["role"], row["inviter_id"]) == (
        ctx.body["id"],
        "member",
        ctx.id(ctx.actor),
    )
    assert secret.startswith(f"lat_inv_{row['prefix']}_") and row["verifier"] == hmac_of(secret)
    assert recent(row["expires_at"], timedelta(days=7)) and row["accepted_at"] is None


def g_invites(ctx: Ctx) -> None:
    unchanged(ctx)
    assert [i["id"] for i in ctx.body["invites"]] == [ctx.world.invite_id]
    (row,) = sql(
        ctx.pg,
        "SELECT role, prefix, inviter_id::text FROM invites WHERE id = %s",
        ctx.world.invite_id,
    )
    (got,) = ctx.body["invites"]
    assert (got["role"], got["prefix"], got["inviter_id"]) == row


def g_revoke_invite(ctx: Ctx) -> None:
    only(ctx.changes, "invites")
    old, new = updated_one(ctx, "invites")
    assert old["id"] == ctx.world.invite_id and recent(new["revoked_at"])


# --- Groups ---------------------------------------------------------------------------------


def g_groups(ctx: Ctx) -> None:
    unchanged(ctx)
    groups = {g["name"]: g for g in ctx.body["groups"]}
    if ctx.actor in MANAGERS:
        rows = sql(
            ctx.pg,
            "SELECT g.name, gm.user_id::text FROM groups g JOIN group_members gm "
            "ON gm.group_id = g.id WHERE g.workspace_id = %s",
            ctx.world.ws,
        )
        got = [(name, m["user_id"]) for name, g in groups.items() for m in g["members"]]
        assert sorted(got) == sorted(rows) and set(groups) == {"G", "H"}
    else:  # a member sees only the names of their own groups
        mine = {"author": {"G"}, "reader": {"G"}, "other": {"H"}}[ctx.actor]
        assert set(groups) == mine and all(set(g) == {"id", "name"} for g in groups.values())


def g_new_group(ctx: Ctx) -> None:
    only(ctx.changes, "groups")
    (row,) = ctx.changes.added["groups"]
    assert (row["id"], row["name"]) == (ctx.body["id"], "Ops")


def g_rename_group(ctx: Ctx) -> None:
    only(ctx.changes, "groups")
    old, new = updated_one(ctx, "groups")
    assert (old["name"], new["name"]) == ("G", "Platform") and ctx.body["name"] == "Platform"


def g_delete_group(ctx: Ctx) -> None:
    only(ctx.changes, "groups", "group_members", "document_grants")
    assert [r["id"] for r in ctx.changes.removed["groups"]] == [ctx.world.groups["G"]]
    assert {r["user_id"] for r in ctx.changes.removed["group_members"]} == {
        ctx.id("author"),
        ctx.id("reader"),
    }
    assert [r["document_id"] for r in ctx.changes.removed["document_grants"]] == [
        ctx.world.docs["shared"]["id"]
    ]
    assert ctx.body == {"grants_removed": 1}


def g_add_to_group(ctx: Ctx) -> None:
    only(ctx.changes, "group_members")
    (row,) = ctx.changes.added["group_members"]
    assert (row["group_id"], row["user_id"]) == (ctx.world.groups["H"], ctx.id("reader"))


def g_remove_from_group(ctx: Ctx) -> None:
    only(ctx.changes, "group_members")
    (row,) = ctx.changes.removed["group_members"]
    assert (row["group_id"], row["user_id"]) == (ctx.world.groups["G"], ctx.id("reader"))


# --- Documents ------------------------------------------------------------------------------

# What each person sees in the document list: (doc, title, serving, pending) by the plan's rules.
VISIBLE = {
    "manager": {
        ("shared", "Checkout runbook v2", "v1", "v2"),
        ("private", "Payments ledger", "v1", None),
        ("draft", "Draft notes", None, "v1"),
    },
    "author": {("shared", "Checkout runbook v2", "v1", "v2"), ("draft", "Draft notes", None, "v1")},
    "reader": {("shared", "Checkout runbook", "v1", None)},
    "other": {("private", "Payments ledger", "v1", None)},
}


def _vname(docs: dict, version_id: str | None) -> str | None:
    if version_id is None:
        return None
    return next(k for d in docs.values() for k, v in d.items() if v == version_id and k != "id")


def g_list_documents(ctx: Ctx) -> None:
    unchanged(ctx)
    docs, names = ctx.world.docs, {d["id"]: n for n, d in ctx.world.docs.items()}
    got = {
        (
            names[d["id"]],
            d["title"],
            _vname(docs, d["serving_version_id"]),
            _vname(docs, d["pending_version_id"]),
        )
        for d in ctx.body["documents"]
    }
    assert got == VISIBLE["manager" if ctx.actor in MANAGERS else ctx.actor]
    for d in ctx.body["documents"]:
        ((author, withdrawn),) = sql(
            ctx.pg,
            "SELECT author_id::text, withdrawn_at IS NOT NULL FROM documents WHERE id = %s",
            d["id"],
        )
        assert (d["author_id"], d["withdrawn"]) == (author, withdrawn)


def g_create_document(ctx: Ctx) -> None:
    only(ctx.changes, "documents", "document_versions")
    (doc,), (version,) = ctx.changes.added["documents"], ctx.changes.added["document_versions"]
    assert doc["author_id"] == version["created_by"] == ctx.id(ctx.actor)
    assert (doc["serving_version_id"], doc["pending_version_id"]) == (None, version["id"])
    assert (version["title"], version["body"]) == ("Rota", "Who is on call.")
    assert ctx.body == {
        "id": doc["id"],
        "author_id": ctx.id(ctx.actor),
        "withdrawn": False,
        "serving_version_id": None,
        "pending_version_id": version["id"],
    }


def g_get_document(ctx: Ctx) -> None:
    unchanged(ctx)
    shared = ctx.world.docs["shared"]
    shown = "v2" if ctx.actor in MANAGERS | {"author"} else "v1"
    title, body = doc_row(ctx, shared[shown])
    assert (ctx.body["shown_version_id"], ctx.body["title"], ctx.body["body"]) == (
        shared[shown],
        title,
        body,
    )
    assert ctx.body["serving_version_id"] == shared["v1"]
    if shown == "v2":  # the author and managers also get history, sharing and the last decision
        assert [v["id"] for v in ctx.body["versions"]] == [shared["v1"], shared["v2"]]
        assert ctx.body["shared_with"] == [{"id": ctx.world.groups["G"], "name": "G"}]
        assert ctx.body["last_decision"] == {
            "decision": "approved",
            "reason": None,
            "version_id": shared["v1"],
        }
    else:
        assert ctx.body["pending_version_id"] is None and "versions" not in ctx.body


def g_version(version: str) -> Callable[[Ctx], None]:
    def ground(ctx: Ctx) -> None:
        unchanged(ctx)
        vid = ctx.world.docs["shared"][version]
        title, body = doc_row(ctx, vid)
        assert ctx.body == {"id": vid, "title": title, "body": body}

    return ground


def g_edit(ctx: Ctx) -> None:
    only(ctx.changes, "documents", "document_versions")
    shared = ctx.world.docs["shared"]
    (version,) = ctx.changes.added["document_versions"]
    assert ctx.changes.removed["document_versions"] == []  # v2 stays: versions are immutable
    assert (version["title"], version["created_by"]) == ("Runbook v3", ctx.id("author"))
    old, new = updated_one(ctx, "documents")
    assert (old["pending_version_id"], new["pending_version_id"]) == (shared["v2"], version["id"])
    assert new["serving_version_id"] == shared["v1"]
    assert ctx.body["pending_version_id"] == version["id"]


def g_review(ctx: Ctx) -> None:
    unchanged(ctx)
    shared = ctx.world.docs["shared"]
    assert (ctx.body["pending_version_id"], ctx.body["serving_version_id"]) == (
        shared["v2"],
        shared["v1"],
    )
    assert ctx.body["title"] == "Checkout runbook v2"
    assert "-Restart the checkout pods." in ctx.body["diff"]
    assert "+Drain checkout." in ctx.body["diff"]


def g_decision(kind: str, reason: str | None = None) -> Callable[[Ctx], None]:
    def ground(ctx: Ctx) -> None:
        only(ctx.changes, "documents", "version_decisions")
        shared = ctx.world.docs["shared"]
        (decision,) = ctx.changes.added["version_decisions"]
        assert (
            decision["decision"],
            decision["version_id"],
            decision["actor_id"],
            decision["reason"],
        ) == (kind, shared["v2"], ctx.id(ctx.actor), reason)
        _old, new = updated_one(ctx, "documents")
        serving = shared["v2"] if kind == "approved" else shared["v1"]
        assert (new["serving_version_id"], new["pending_version_id"]) == (serving, None)
        if kind == "approved":
            assert ctx.body == {"serving_version_id": shared["v2"]}
            assert ctx.audit[0]["detail"] == {"self_approved": False}

    return ground


def g_withdraw(ctx: Ctx) -> None:
    only(ctx.changes, "documents")
    old, new = updated_one(ctx, "documents")
    assert old["withdrawn_at"] is None and recent(new["withdrawn_at"])
    assert (old["pending_version_id"], new["pending_version_id"]) == (
        ctx.world.docs["shared"]["v2"],
        None,
    )


def g_share(doc: str, group: str) -> Callable[[Ctx], None]:
    def ground(ctx: Ctx) -> None:
        only(ctx.changes, "document_grants")
        (grant,) = ctx.changes.added["document_grants"]
        assert (grant["document_id"], grant["group_id"]) == (
            ctx.world.docs[doc]["id"],
            ctx.world.groups[group],
        )

    return ground


def g_unshare(ctx: Ctx) -> None:
    only(ctx.changes, "document_grants")
    (grant,) = ctx.changes.removed["document_grants"]
    assert (grant["document_id"], grant["group_id"]) == (
        ctx.world.docs["shared"]["id"],
        ctx.world.groups["G"],
    )


def g_reassign(ctx: Ctx) -> None:
    only(ctx.changes, "documents")
    old, new = updated_one(ctx, "documents")
    assert (old["author_id"], new["author_id"]) == (ctx.id("author"), ctx.id("reader"))


# Retrieval returns approved versions only, for every role (R19); keyed by (doc, version).
RETRIEVABLE = {
    "manager": {("shared", "v1"), ("private", "v1")},
    "author": {("shared", "v1")},  # own approved document; the draft is never retrieved
    "reader": {("shared", "v1")},
    "other": {("private", "v1")},
}


def retrievable(world: World, actor: str) -> list[str]:
    want = RETRIEVABLE["manager" if actor in MANAGERS else actor]
    return sorted(world.docs[d][v] for d, v in want)


def ground_search_results(pg: dict, results: list[dict]) -> None:
    """Each result's title and approver come from the database; snippets mark the match."""
    for r in results:
        ((title, approver),) = sql(
            pg,
            "SELECT v.title, u.display_name FROM document_versions v JOIN version_decisions d "
            "ON d.version_id = v.id AND d.decision = 'approved' JOIN users u ON u.id = d.actor_id "
            "WHERE v.id = %s",
            r["version_id"],
        )
        assert (r["title"], r["approved_by"]) == (title, approver)
        assert "[[" in r["snippet"] and "checkout" in r["snippet"].lower()


def g_search(ctx: Ctx) -> None:
    unchanged(ctx)
    got = [r["version_id"] for r in ctx.body["results"]]
    assert sorted(got) == retrievable(ctx.world, ctx.actor)
    assert [str(v) for v in ctx.audit[0]["returned_version_ids"]] == got  # audited exactly
    ground_search_results(ctx.pg, ctx.body["results"])


def g_audit(ctx: Ctx) -> None:
    unchanged(ctx)
    rows = sql(
        ctx.pg,
        "SELECT id, action, outcome FROM audit_events WHERE workspace_id = %s AND id <= %s "
        "ORDER BY id DESC LIMIT 100",
        ctx.world.ws,
        ctx.body["events"][0]["id"],
    )
    assert [(e["id"], e["action"], e["outcome"]) for e in ctx.body["events"]] == rows


# --- Tokens ---------------------------------------------------------------------------------


def g_new_token(ctx: Ctx) -> None:
    only(ctx.changes, "access_tokens")
    (row,) = ctx.changes.added["access_tokens"]
    token = ctx.body["token"]
    assert (row["user_id"], row["name"], row["id"]) == (ctx.id(ctx.actor), "ci", ctx.body["id"])
    assert token.startswith(f"lat_pat_{row['prefix']}_") and row["verifier"] == hmac_of(token)
    assert recent(row["expires_at"], timedelta(days=90))


def g_tokens(ctx: Ctx) -> None:
    unchanged(ctx)
    assert [t["id"] for t in ctx.body["tokens"]] == [ctx.world.tokens[ctx.actor]]
    assert "token" not in ctx.body["tokens"][0]  # the secret is never shown again


def g_revoke_token(ctx: Ctx) -> None:
    only(ctx.changes, "access_tokens")
    old, new = updated_one(ctx, "access_tokens")
    assert old["id"] == ctx.world.tokens[ctx.actor] and recent(new["revoked_at"])
