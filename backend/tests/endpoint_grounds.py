"""Grounding checks for the endpoint matrix: each compares a response with the database.

Expected visibility (VISIBLE, RETRIEVABLE) is written out from the plan's rules for the known
world in tests/endpoint_world.py, not derived from app.policy, so it stays an independent oracle.
"""

import hmac
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from pydantic import SecretStr

from app.ai import keys
from app.config import DEV_AI_KEY, AiModel, AiProvider, Settings
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


def derived_once(ctx: Ctx, version: dict) -> None:
    """A new draft is chunked and embedded in its request (derive_inline): one span, ready."""
    (derivation,), (chunk,) = ctx.changes.added["derivations"], ctx.changes.added["chunks"]
    assert (derivation["version_id"], derivation["status"]) == (version["id"], "ready")
    body = version["body"]
    assert (chunk["version_id"], chunk["config_hash"], chunk["start"], chunk["end"]) == (
        version["id"],
        derivation["config_hash"],
        0,
        len(body),
    )
    assert chunk["sha256"] == sha256(body.encode()).hexdigest()


RETAINED = "Version bodies are retained: document versions are immutable."


def purged(ctx: Ctx, reason: str, *versions: str) -> None:
    """The shared document's named versions lost their derived rows, receipted (KTD8)."""
    shared = ctx.world.docs["shared"]
    ids = {shared[v] for v in versions}
    gone = {t: [r["version_id"] for r in ctx.changes.removed[t]] for t in ("derivations", "chunks")}
    assert set(gone["derivations"]) == set(gone["chunks"]) == ids, gone
    (receipt,) = [r for r in ctx.audit if r["channel"] == "system"]
    assert (receipt["actor_user_id"], receipt["action"], str(receipt["document_id"])) == (
        None,
        "purge",
        shared["id"],
    )
    removed = {"derivations": len(gone["derivations"]), "chunks": len(gone["chunks"])}
    assert receipt["detail"] == {
        "reason": reason,
        "removed": removed | {"intake_payloads": 0},  # the world's documents are typed
        "note": RETAINED,
    }


def g_create_document(ctx: Ctx) -> None:
    only(ctx.changes, "documents", "document_versions", "derivations", "chunks")
    (doc,), (version,) = ctx.changes.added["documents"], ctx.changes.added["document_versions"]
    derived_once(ctx, version)
    assert not any(ctx.changes.removed.values()) and ctx.audit[1:] == []  # nothing to purge
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


UPLOADED = b"Who is on call."  # a clean Markdown file: extracted unchanged, no flags


def uploaded(ctx: Ctx, version: dict) -> None:
    """An accepted upload: its permanent intake, then its purgeable payload (KTD11, KTD13)."""
    (intake,), (payload,) = ctx.changes.added["intakes"], ctx.changes.added["intake_payloads"]
    assert (
        intake["outcome"],
        intake["document_id"],
        intake["version_id"],
        intake["actor_id"],
        intake["filename"],
        intake["detected_type"],
        intake["source_sha256"],
    ) == (
        "accepted",
        version["document_id"],
        version["id"],
        ctx.id(ctx.actor),
        "rota.md",
        "md",
        sha256(UPLOADED).hexdigest(),
    )
    assert (payload["intake_id"], payload["version_id"], payload["original"]) == (
        intake["id"],
        version["id"],
        UPLOADED.hex(),
    )
    assert payload["hidden_excerpts"] == ()


def g_upload(ctx: Ctx) -> None:
    only(
        ctx.changes,
        "documents",
        "document_versions",
        "derivations",
        "chunks",
        "intakes",
        "intake_payloads",
    )
    (doc,), (version,) = ctx.changes.added["documents"], ctx.changes.added["document_versions"]
    derived_once(ctx, version)
    uploaded(ctx, version)
    assert not any(ctx.changes.removed.values()) and ctx.audit[1:] == []  # nothing to purge
    assert doc["author_id"] == version["created_by"] == ctx.id(ctx.actor)
    assert (version["title"], version["body"]) == ("rota", UPLOADED.decode())
    assert ctx.body == {
        "id": doc["id"],
        "author_id": ctx.id(ctx.actor),
        "withdrawn": False,
        "serving_version_id": None,
        "pending_version_id": version["id"],
        "created": True,
    }


def g_upload_version(ctx: Ctx) -> None:
    only(
        ctx.changes,
        "documents",
        "document_versions",
        "derivations",
        "chunks",
        "intakes",
        "intake_payloads",
    )
    shared = ctx.world.docs["shared"]
    (version,) = ctx.changes.added["document_versions"]
    derived_once(ctx, version)
    uploaded(ctx, version)
    purged(ctx, "replaced", "v2")
    # With no title field, a new version keeps its document's current title.
    assert (version["title"], version["body"]) == ("Checkout runbook v2", UPLOADED.decode())
    old, new = updated_one(ctx, "documents")
    assert (old["pending_version_id"], new["pending_version_id"]) == (shared["v2"], version["id"])
    assert new["serving_version_id"] == shared["v1"]
    assert (ctx.body["pending_version_id"], ctx.body["created"]) == (version["id"], True)


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
        ready = {"status": "ready", "error": None}
        assert ctx.body["derivations"] == {shared["v2"]: ready, shared["v1"]: ready}
        assert ctx.body["intakes"] == {}  # the known world's documents were typed, not uploaded
    else:
        assert ctx.body["pending_version_id"] is None and "versions" not in ctx.body
        assert "derivations" not in ctx.body and "intakes" not in ctx.body


def g_version(version: str) -> Callable[[Ctx], None]:
    def ground(ctx: Ctx) -> None:
        unchanged(ctx)
        vid = ctx.world.docs["shared"][version]
        title, body = doc_row(ctx, vid)
        assert ctx.body == {"id": vid, "title": title, "body": body}

    return ground


def g_edit(ctx: Ctx) -> None:
    only(ctx.changes, "documents", "document_versions", "derivations", "chunks")
    shared = ctx.world.docs["shared"]
    (version,) = ctx.changes.added["document_versions"]
    derived_once(ctx, version)
    purged(ctx, "replaced", "v2")
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
    assert (ctx.body["receipt"], ctx.body["intake"]) == (None, None)  # typed, not uploaded
    assert ctx.body["derivation"] == {"status": "ready", "error": None}


def g_decision(kind: str, reason: str | None = None) -> Callable[[Ctx], None]:
    def ground(ctx: Ctx) -> None:
        only(ctx.changes, "documents", "version_decisions", "derivations", "chunks")
        superseded = kind == "approved"  # approving v2 supersedes v1; otherwise v2 itself goes
        purged(ctx, "superseded" if superseded else kind, "v1" if superseded else "v2")
        shared = ctx.world.docs["shared"]
        (decision,) = ctx.changes.added["version_decisions"]
        assert (
            decision["decision"],
            decision["version_id"],
            decision["actor_id"],
            decision["reason"],
            decision["acknowledged_receipt_sha256"],  # typed drafts have no receipt
        ) == (kind, shared["v2"], ctx.id(ctx.actor), reason, None)
        _old, new = updated_one(ctx, "documents")
        serving = shared["v2"] if kind == "approved" else shared["v1"]
        assert (new["serving_version_id"], new["pending_version_id"]) == (serving, None)
        if kind == "approved":
            assert ctx.body == {"serving_version_id": shared["v2"]}
            assert ctx.audit[0]["detail"] == {"self_approved": False}

    return ground


def g_withdraw(ctx: Ctx) -> None:
    only(ctx.changes, "documents", "derivations", "chunks")
    purged(ctx, "withdrawn", "v1", "v2")
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


def g_search_history(ctx: Ctx) -> None:
    unchanged(ctx)
    rows = sql(
        ctx.pg,
        "SELECT detail->>'query' FROM audit_events WHERE workspace_id = %s AND actor_user_id = %s "
        "AND action = 'search' AND outcome = 'allow' ORDER BY id DESC LIMIT 10",
        ctx.world.ws,
        ctx.id(ctx.actor),
    )
    assert [s["query"] for s in ctx.body["searches"]] == [query for (query,) in rows]


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


# --- Entities ----------------------------------------------------------------------------------

# Readable mentions of "checkout"/"payments", written out from the world (R22).
_MENTIONED = {
    "owner": {"shared", "private"},
    "admin": {"shared", "private"},
    "author": {"shared"},
    "reader": {"shared"},
    "other": {"private"},
}


def _aliases_seen(actor: str) -> list[str]:
    return ["payments"] if "private" in _MENTIONED[actor] else []


def g_entities(ctx: Ctx) -> None:
    unchanged(ctx)
    assert ctx.body["entities"] == [
        {"id": ctx.world.entity, "name": "checkout", "aliases": _aliases_seen(ctx.actor)}
    ]


def g_entity(ctx: Ctx) -> None:
    unchanged(ctx)
    want = {ctx.world.docs[name]["v1"] for name in _MENTIONED[ctx.actor]}
    assert {m["version_id"] for m in ctx.body["mentions"]} == want
    assert ctx.body["aliases"] == _aliases_seen(ctx.actor) and ctx.body["co_mentions"] == []
    for mention in ctx.body["mentions"]:
        _, body = doc_row(ctx, mention["version_id"])
        for span in mention["spans"]:
            assert body[span["start"] : span["end"]].lower() == span["term"]
    (row,) = ctx.audit
    assert {str(v) for v in row["returned_version_ids"]} == want


def g_new_entity(ctx: Ctx) -> None:
    only(ctx.changes, "entities", "entity_aliases")
    (row,) = ctx.changes.added["entities"]
    (alias,) = ctx.changes.added["entity_aliases"]
    assert (row["id"], row["name"], alias["entity_id"], alias["alias"]) == (
        ctx.body["id"],
        "ledger",
        ctx.body["id"],
        "books",
    )


def g_rename_entity(ctx: Ctx) -> None:
    only(ctx.changes, "entities")
    old, new = updated_one(ctx, "entities")
    assert (old["name"], new["name"]) == ("checkout", "till") and ctx.body["name"] == "till"


def g_delete_entity(ctx: Ctx) -> None:
    only(ctx.changes, "entities", "entity_aliases")
    assert [r["id"] for r in ctx.changes.removed["entities"]] == [ctx.world.entity]
    assert [r["alias"] for r in ctx.changes.removed["entity_aliases"]] == ["payments"]


# --- AI settings ----------------------------------------------------------------------------

AI_PROVIDER = AiProvider(
    id="fake",
    label="Fake",
    kind="fake",
    models=[AiModel(id="fake-1", label="Fake 1", context_tokens=8000, max_output_tokens=500)],
)
MATRIX_KEY = "sk-matrix-SECRET-key"
_PROVIDERS = [{"id": "fake", "label": "Fake", "models": [{"id": "fake-1", "label": "Fake 1"}]}]


def seed_ai(world: World, pg: dict) -> None:
    """Connect the world's workspace (a stand-in ciphertext: these calls never decrypt it)."""
    sql(
        pg,
        "INSERT INTO ai_settings (workspace_id, provider_id, model, encrypted_key, retention_days)"
        " VALUES (%s, 'fake', 'fake-1', 'sealed', 45) RETURNING workspace_id",
        world.ws,
    )


def _ai_view(ctx: Ctx, enabled: bool, retention: int) -> dict:
    view = {
        "providers": _PROVIDERS,
        "enabled": enabled,
        "provider_id": "fake" if enabled else None,
        "model": "fake-1" if enabled else None,
        "retention_days": retention,
    }
    return view | ({"key_status": "ok"} if ctx.actor in MANAGERS else {})


def g_ai_settings(ctx: Ctx) -> None:
    unchanged(ctx)
    assert ctx.body == _ai_view(ctx, True, 45)  # members never see the key status (R30)


def g_connect_ai(ctx: Ctx) -> None:
    only(ctx.changes, "ai_settings")
    (row,) = ctx.changes.added["ai_settings"]
    assert (row["provider_id"], row["model"], row["key_status"], row["retention_days"]) == (
        "fake",
        "fake-1",
        "ok",
        30,
    )
    assert MATRIX_KEY.encode().hex() not in row["encrypted_key"]  # stored encrypted (R4)
    assert ctx.body == _ai_view(ctx, True, 30)
    assert ctx.audit[0]["detail"] == {
        "op": "save",
        "changed": {"provider_id": [None, "fake"], "model": [None, "fake-1"]},
        "key_replaced": True,
    }
    assert MATRIX_KEY not in repr(ctx.audit)


def g_disconnect_ai(ctx: Ctx) -> None:
    only(ctx.changes, "ai_settings")
    old, new = ctx.changes.updated("ai_settings", "workspace_id")[0]
    assert old["encrypted_key"] == b"sealed".hex()
    assert (new["provider_id"], new["model"], new["encrypted_key"], new["retention_days"]) == (
        None,
        None,
        None,
        45,
    )
    assert ctx.audit[0]["detail"] == {"op": "disconnect", "provider_id": "fake"}


# --- Conversations --------------------------------------------------------------------------

# Each member's conversation cites a version they may read; "other" wrote the private ledger.
_CITES = {
    "owner": "shared",
    "admin": "shared",
    "author": "shared",
    "reader": "shared",
    "other": "private",
}
_SPAN = (0, 7)


def conversation_of(world: World, actor: str) -> str:
    """Each member's own conversation; outsiders and anonymous callers try the author's."""
    return str(uuid.uuid5(uuid.UUID(world.ws), actor if actor in MEMBERS else "author"))


def seed_conversations(world: World, pg: dict) -> None:
    """One conversation per member, each with one turn citing a span it may read."""
    for actor, doc in _CITES.items():
        conversation = conversation_of(world, actor)
        sql(
            pg,
            "INSERT INTO conversations (id, workspace_id, user_id, title) VALUES (%s, %s, %s, %s)"
            " RETURNING id",
            conversation,
            world.ws,
            world.users[actor],
            f"{actor} asks",
        )
        ((turn,),) = sql(
            pg,
            "INSERT INTO turns (workspace_id, conversation_id, ordinal, question, sentences, "
            "outcome, provider_id, model) VALUES (%s, %s, 1, 'Restart?', '[]', 'answered', "
            "'fake', 'fake-1') RETURNING id",
            world.ws,
            conversation,
        )
        sql(
            pg,
            "INSERT INTO turn_passages (turn_id, ordinal, workspace_id, document_id, version_id, "
            "start, \"end\", sha256) VALUES (%s, 1, %s, %s, %s, %s, %s, 'x') RETURNING turn_id",
            turn,
            world.ws,
            world.docs[doc]["id"],
            world.docs[doc]["v1"],
            *_SPAN,
        )


def _own_conversations(ctx: Ctx) -> list[str]:
    return [
        str(c)
        for (c,) in sql(
            ctx.pg,
            "SELECT id FROM conversations WHERE workspace_id = %s AND user_id = %s",
            ctx.world.ws,
            ctx.id(ctx.actor),
        )
    ]


def g_new_conversation(ctx: Ctx) -> None:
    only(ctx.changes, "conversations")
    (row,) = ctx.changes.added["conversations"]
    assert (row["id"], row["user_id"], row["title"]) == (ctx.body["id"], ctx.id(ctx.actor), "Pods")
    assert ctx.body["title"] == "Pods" and recent(row["last_turn_at"])


def g_conversations(ctx: Ctx) -> None:
    unchanged(ctx)
    assert [c["id"] for c in ctx.body["conversations"]] == _own_conversations(ctx)
    assert _own_conversations(ctx) == [conversation_of(ctx.world, ctx.actor)]


def g_conversation(ctx: Ctx) -> None:
    unchanged(ctx)
    version = ctx.world.docs[_CITES[ctx.actor]]["v1"]
    title, body = doc_row(ctx, version)
    assert (ctx.body["id"], ctx.body["left_out"]) == (conversation_of(ctx.world, ctx.actor), 0)
    ((turn,),) = [ctx.body["turns"]]
    ((passage,),) = [turn["passages"]]
    assert (turn["question"], passage["version_id"], passage["title"]) == (
        "Restart?",
        version,
        title,
    )
    assert passage["text"] == body[slice(*_SPAN)]  # re-read from the version body (KTD4)


def g_delete_conversation(ctx: Ctx) -> None:
    only(ctx.changes, "conversations")
    own = conversation_of(ctx.world, ctx.actor)
    assert [r["id"] for r in ctx.changes.removed["conversations"]] == [own]
    assert ctx.changes.added["conversations"] == []
    assert sql(ctx.pg, "SELECT count(*) FROM turns WHERE conversation_id = %s", own) == [(0,)]


# --- Ask turns ------------------------------------------------------------------------------

ASKED = "How do I restart checkout?"


def sse(text: str) -> list[tuple[str, dict]]:
    """A turn's server-sent events as (name, data) pairs."""
    blocks = [block.split("\n") for block in text.split("\n\n") if block]
    return [(n.removeprefix("event: "), json.loads(d.removeprefix("data: "))) for n, d in blocks]


def seed_ask(world: World, pg: dict) -> None:
    """Connect the fake provider with a key sealed as a save would (a turn decrypts it), and give
    each member a conversation."""
    sealing = Settings.model_construct(ai_key_encryption_keys=[SecretStr(DEV_AI_KEY)])
    sealed = keys.encrypt(sealing, uuid.UUID(world.ws), "fake", MATRIX_KEY)
    sql(
        pg,
        "INSERT INTO ai_settings (workspace_id, provider_id, model, encrypted_key)"
        " VALUES (%s, 'fake', 'fake-1', %s) RETURNING workspace_id",
        world.ws,
        sealed,
    )
    seed_conversations(world, pg)


def g_ask(ctx: Ctx) -> None:
    only(ctx.changes, "conversations")
    own = conversation_of(ctx.world, ctx.actor)
    old, new = updated_one(ctx, "conversations")
    assert old["id"] == own and (new["in_flight_turn"], new["in_flight_at"]) == (2, None)
    assert new["last_turn_at"] > old["last_turn_at"]

    names = [name for name, _ in ctx.body["events"]]
    assert (names[0], set(names[1:-1]), names[-1]) == ("passages", {"sentence"}, "done")
    passages = ctx.body["events"][0][1]["passages"]
    versions = sorted({p["version_id"] for p in passages})
    assert set(versions) <= set(retrievable(ctx.world, ctx.actor))
    saved = sql(
        ctx.pg,
        "SELECT p.ordinal, p.version_id::text FROM turns t JOIN turn_passages p "
        "ON p.turn_id = t.id WHERE t.conversation_id = %s AND t.ordinal = 2 ORDER BY p.ordinal",
        own,
    )
    assert saved == [(int(p["id"][1:]), p["version_id"]) for p in passages]

    # Both rows ground the turn: what was sent and to whom, then the outcome, never the answer.
    sent, outcome = ctx.audit
    assert sorted(str(v) for v in sent["returned_version_ids"]) == versions
    assert sent["detail"] | {"count": 0} == {
        "conversation_id": own,
        "turn": 2,
        "query": ASKED,
        "count": 0,
        "provider_id": "fake",
        "model": "fake-1",
    }
    assert outcome["detail"] == {"conversation_id": own, "turn": 2, "outcome": "answered"}
    answer = [data["text"] for name, data in ctx.body["events"] if name == "sentence"]
    assert not any(text in repr(ctx.audit) for text in answer) and MATRIX_KEY not in repr(ctx.audit)
