"""Every workspace endpoint, called by every kind of person, with every result grounded.

For each (endpoint, actor) the test builds a fresh known world, makes one call, and checks:
  1. the status against the role table in the plan (an expectation written here, not
     derived from policy.py);
  2. the response body against the database;
  3. the exact set of rows the call changed (a refused call changes nothing);
  4. the audit rows the call wrote: one allow or deny row for members, none for outsiders.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from pydantic import SecretStr

from app.config import DEV_AI_KEY
from app.main import create_app
from tests.endpoint_grounds import (
    AI_PROVIDER,
    EVERY_MEMBER,
    MANAGERS,
    MATRIX_KEY,
    NOT_FOUND,
    UPLOADED,
    Ctx,
    conversation_of,
    g_add_to_group,
    g_ai_settings,
    g_audit,
    g_connect_ai,
    g_conversation,
    g_conversations,
    g_create_document,
    g_decision,
    g_delete_conversation,
    g_delete_entity,
    g_delete_group,
    g_disconnect_ai,
    g_edit,
    g_entities,
    g_entity,
    g_get_document,
    g_groups,
    g_invites,
    g_list_documents,
    g_members,
    g_new_conversation,
    g_new_entity,
    g_new_group,
    g_new_invite,
    g_new_token,
    g_reassign,
    g_remove_from_group,
    g_remove_member,
    g_rename_entity,
    g_rename_group,
    g_review,
    g_revoke_invite,
    g_revoke_token,
    g_role,
    g_search,
    g_search_history,
    g_share,
    g_tokens,
    g_transfer,
    g_unshare,
    g_upload,
    g_upload_version,
    g_version,
    g_withdraw,
    seed_ai,
    seed_conversations,
    unchanged,
)
from tests.endpoint_world import (
    ACTORS,
    MEMBERS,
    World,
    audit_rows,
    build_world,
    diff,
    last_audit_id,
    snapshot,
)
from tests.helpers import PASSWORD


@dataclass
class Case:
    name: str
    method: str
    template: str  # the OpenAPI path this case covers
    request: Callable[[World, str], tuple[str, dict | None]]  # (world, actor)
    allowed: set[str]
    status: int
    action: str
    ground: Callable[[Ctx], None]
    audited_when_allowed: Callable[[str], bool] = lambda actor: True
    multipart: bool = False  # the request body is httpx `files`, not JSON
    prepare: Callable[[World, dict], None] = lambda world, pg: None  # extra rows, before the call


# --- The matrix -----------------------------------------------------------------------------

W = "/api/workspaces/{workspace_id}"
D = W + "/documents/{document_id}"


def _v(w: World, version: str, **extra) -> dict:
    return {"version_id": w.docs["shared"][version], **extra}


CASES = [
    Case(
        "list members",
        "GET",
        W + "/members",
        lambda w, a: (w.url("/members"), None),
        MANAGERS,
        200,
        "view_roster",
        g_members,
    ),
    Case(
        "promote a member",
        "PATCH",
        W + "/members/{user_id}",
        lambda w, a: (w.url(f"/members/{w.users['other']}"), {"role": "admin"}),
        {"owner"},
        200,
        "change_role",
        g_role,
    ),
    Case(
        "remove a member",
        "DELETE",
        W + "/members/{user_id}",
        lambda w, a: (w.url(f"/members/{w.users['other']}"), None),
        MANAGERS,
        204,
        "remove_member",
        g_remove_member,
    ),
    Case(
        "transfer ownership",
        "POST",
        W + "/transfer",
        lambda w, a: (w.url("/transfer"), {"user_id": w.users["admin"]}),
        {"owner"},
        200,
        "transfer_ownership",
        g_transfer,
    ),
    Case(
        "invite a member",
        "POST",
        W + "/invites",
        lambda w, a: (w.url("/invites"), {"role": "member"}),
        MANAGERS,
        201,
        "invite",
        g_new_invite,
    ),
    Case(
        "list invites",
        "GET",
        W + "/invites",
        lambda w, a: (w.url("/invites"), None),
        MANAGERS,
        200,
        "view_roster",
        g_invites,
    ),
    Case(
        "revoke an invite",
        "DELETE",
        W + "/invites/{invite_id}",
        lambda w, a: (w.url(f"/invites/{w.invite_id}"), None),
        MANAGERS,
        204,
        "revoke_invite",
        g_revoke_invite,
    ),
    # Members are allowed a filtered list of their own groups, which is not audited.
    Case(
        "list groups",
        "GET",
        W + "/groups",
        lambda w, a: (w.url("/groups"), None),
        EVERY_MEMBER,
        200,
        "view_roster",
        g_groups,
        audited_when_allowed=lambda actor: actor in MANAGERS,
    ),
    Case(
        "create a group",
        "POST",
        W + "/groups",
        lambda w, a: (w.url("/groups"), {"name": "Ops"}),
        MANAGERS,
        201,
        "manage_groups",
        g_new_group,
    ),
    Case(
        "rename a group",
        "PATCH",
        W + "/groups/{group_id}",
        lambda w, a: (w.url(f"/groups/{w.groups['G']}"), {"name": "Platform"}),
        MANAGERS,
        200,
        "manage_groups",
        g_rename_group,
    ),
    Case(
        "delete a group",
        "DELETE",
        W + "/groups/{group_id}",
        lambda w, a: (w.url(f"/groups/{w.groups['G']}"), None),
        MANAGERS,
        200,
        "manage_groups",
        g_delete_group,
    ),
    Case(
        "add to a group",
        "PUT",
        W + "/groups/{group_id}/members/{user_id}",
        lambda w, a: (w.url(f"/groups/{w.groups['H']}/members/{w.users['reader']}"), None),
        MANAGERS,
        204,
        "manage_groups",
        g_add_to_group,
    ),
    Case(
        "remove from a group",
        "DELETE",
        W + "/groups/{group_id}/members/{user_id}",
        lambda w, a: (w.url(f"/groups/{w.groups['G']}/members/{w.users['reader']}"), None),
        MANAGERS,
        204,
        "manage_groups",
        g_remove_from_group,
    ),
    Case(
        "list documents",
        "GET",
        W + "/documents",
        lambda w, a: (w.url("/documents"), None),
        EVERY_MEMBER,
        200,
        "list_documents",
        g_list_documents,
    ),
    Case(
        "create a document",
        "POST",
        W + "/documents",
        lambda w, a: (w.url("/documents"), {"title": "Rota", "body": "Who is on call."}),
        EVERY_MEMBER,
        201,
        "create_document",
        g_create_document,
    ),
    Case(
        "upload a document",
        "POST",
        W + "/documents/upload",
        lambda w, a: (w.url("/documents/upload"), {"file": ("rota.md", UPLOADED)}),
        EVERY_MEMBER,
        201,
        "create_document",
        g_upload,
        multipart=True,
    ),
    Case(
        "upload a new version",
        "POST",
        W + "/documents/upload",
        lambda w, a: (
            w.url("/documents/upload"),
            {"file": ("rota.md", UPLOADED), "document_id": (None, w.docs["shared"]["id"])},
        ),
        {"author"},
        201,
        "edit",
        g_upload_version,
        multipart=True,
    ),
    Case(
        "view a shared document",
        "GET",
        D,
        lambda w, a: (w.doc("shared"), None),
        MANAGERS | {"author", "reader"},
        200,
        "view",
        g_get_document,
    ),
    Case(
        "view the approved version",
        "GET",
        D + "/versions/{version_id}",
        lambda w, a: (w.doc("shared", f"/versions/{w.docs['shared']['v1']}"), None),
        MANAGERS | {"author", "reader"},
        200,
        "view",
        g_version("v1"),
    ),
    Case(
        "view the pending version",
        "GET",
        D + "/versions/{version_id}",
        lambda w, a: (w.doc("shared", f"/versions/{w.docs['shared']['v2']}"), None),
        MANAGERS | {"author"},
        200,
        "view",
        g_version("v2"),
    ),
    Case(
        "edit a document",
        "PUT",
        D,
        lambda w, a: (w.doc("shared"), {"title": "Runbook v3", "body": "Scale checkout."}),
        {"author"},
        200,
        "edit",
        g_edit,
    ),
    Case(
        "review a draft",
        "GET",
        D + "/review",
        lambda w, a: (w.doc("shared", "/review"), None),
        MANAGERS,
        200,
        "review",
        g_review,
    ),
    Case(
        "approve a draft",
        "POST",
        D + "/approve",
        lambda w, a: (w.doc("shared", "/approve"), _v(w, "v2")),
        MANAGERS,
        200,
        "approve",
        g_decision("approved"),
    ),
    Case(
        "reject a draft",
        "POST",
        D + "/reject",
        lambda w, a: (w.doc("shared", "/reject"), _v(w, "v2", reason="Too vague")),
        MANAGERS,
        204,
        "reject",
        g_decision("rejected", "Too vague"),
    ),
    Case(
        "discard a draft",
        "POST",
        D + "/discard",
        lambda w, a: (w.doc("shared", "/discard"), _v(w, "v2")),
        {"author"},
        204,
        "discard",
        g_decision("discarded"),
    ),
    Case(
        "withdraw a document",
        "POST",
        D + "/withdraw",
        lambda w, a: (w.doc("shared", "/withdraw"), None),
        MANAGERS | {"author"},
        204,
        "withdraw",
        g_withdraw,
    ),
    Case(
        "share with any group",
        "PUT",
        D + "/shares/{group_id}",
        lambda w, a: (w.doc("shared", f"/shares/{w.groups['H']}"), None),
        MANAGERS,
        204,
        "share",
        g_share("shared", "H"),
    ),
    Case(
        "share with an own group",
        "PUT",
        D + "/shares/{group_id}",
        lambda w, a: (w.doc("draft", f"/shares/{w.groups['G']}"), None),
        MANAGERS | {"author"},
        204,
        "share",
        g_share("draft", "G"),
    ),
    Case(
        "unshare",
        "DELETE",
        D + "/shares/{group_id}",
        lambda w, a: (w.doc("shared", f"/shares/{w.groups['G']}"), None),
        MANAGERS | {"author"},
        204,
        "unshare",
        g_unshare,
    ),
    Case(
        "reassign the author",
        "POST",
        D + "/author",
        lambda w, a: (w.doc("shared", "/author"), {"user_id": w.users["reader"]}),
        MANAGERS,
        204,
        "reassign_author",
        g_reassign,
    ),
    Case(
        "search",
        "GET",
        W + "/search",
        lambda w, a: (w.url("/search?q=checkout"), None),
        EVERY_MEMBER,
        200,
        "search",
        g_search,
    ),
    Case(
        "list own searches",
        "GET",
        W + "/search/history",
        lambda w, a: (w.url("/search/history"), None),
        EVERY_MEMBER,
        200,
        "view_own_searches",
        g_search_history,
    ),
    Case(
        "list entities",
        "GET",
        W + "/entities",
        lambda w, a: (w.url("/entities"), None),
        EVERY_MEMBER,
        200,
        "read_graph",
        g_entities,
    ),
    Case(
        "read an entity",
        "GET",
        W + "/entities/{entity_id}",
        lambda w, a: (w.url(f"/entities/{w.entity}"), None),
        EVERY_MEMBER,
        200,
        "read_graph",
        g_entity,
    ),
    Case(
        "create an entity",
        "POST",
        W + "/entities",
        lambda w, a: (w.url("/entities"), {"name": "ledger", "aliases": ["books"]}),
        MANAGERS,
        201,
        "manage_entities",
        g_new_entity,
    ),
    Case(
        "rename an entity",
        "PATCH",
        W + "/entities/{entity_id}",
        lambda w, a: (w.url(f"/entities/{w.entity}"), {"name": "till"}),
        MANAGERS,
        200,
        "manage_entities",
        g_rename_entity,
    ),
    Case(
        "delete an entity",
        "DELETE",
        W + "/entities/{entity_id}",
        lambda w, a: (w.url(f"/entities/{w.entity}"), None),
        MANAGERS,
        204,
        "manage_entities",
        g_delete_entity,
    ),
    Case(
        "read the audit trail",
        "GET",
        W + "/audit",
        lambda w, a: (w.url("/audit"), None),
        MANAGERS,
        200,
        "view_audit",
        g_audit,
    ),
    Case(
        "create a token",
        "POST",
        W + "/tokens",
        lambda w, a: (w.url("/tokens"), {"name": "ci", "password": PASSWORD}),
        EVERY_MEMBER,
        201,
        "manage_own_tokens",
        g_new_token,
    ),
    Case(
        "list own tokens",
        "GET",
        W + "/tokens",
        lambda w, a: (w.url("/tokens"), None),
        EVERY_MEMBER,
        200,
        "manage_own_tokens",
        g_tokens,
    ),
    # Each member revokes their own token; outsiders and anonymous callers try the author's.
    Case(
        "revoke own token",
        "DELETE",
        W + "/tokens/{token_id}",
        lambda w, a: (w.url(f"/tokens/{w.tokens.get(a, w.tokens['author'])}"), None),
        EVERY_MEMBER,
        204,
        "manage_own_tokens",
        g_revoke_token,
    ),
    Case(
        "view AI settings",
        "GET",
        W + "/ai/settings",
        lambda w, a: (w.url("/ai/settings"), None),
        EVERY_MEMBER,
        200,
        "view_ai_settings",
        g_ai_settings,
        prepare=seed_ai,
    ),
    Case(
        "connect AI",
        "PUT",
        W + "/ai/settings",
        lambda w, a: (
            w.url("/ai/settings"),
            {"provider_id": "fake", "model": "fake-1", "retention_days": 30, "api_key": MATRIX_KEY},
        ),
        MANAGERS,
        200,
        "manage_ai_settings",
        g_connect_ai,
    ),
    Case(
        "disconnect AI",
        "DELETE",
        W + "/ai/settings",
        lambda w, a: (w.url("/ai/settings"), None),
        MANAGERS,
        204,
        "manage_ai_settings",
        g_disconnect_ai,
        prepare=seed_ai,
    ),
    Case(
        "start a conversation",
        "POST",
        W + "/conversations",
        lambda w, a: (w.url("/conversations"), {"title": "Pods"}),
        EVERY_MEMBER,
        201,
        "manage_own_conversations",
        g_new_conversation,
    ),
    Case(
        "list own conversations",
        "GET",
        W + "/conversations",
        lambda w, a: (w.url("/conversations"), None),
        EVERY_MEMBER,
        200,
        "manage_own_conversations",
        g_conversations,
        prepare=seed_conversations,
    ),
    # Each member opens their own conversation; outsiders and anonymous callers try the author's.
    Case(
        "reopen own conversation",
        "GET",
        W + "/conversations/{conversation_id}",
        lambda w, a: (w.url(f"/conversations/{conversation_of(w, a)}"), None),
        EVERY_MEMBER,
        200,
        "manage_own_conversations",
        g_conversation,
        prepare=seed_conversations,
    ),
    Case(
        "delete own conversation",
        "DELETE",
        W + "/conversations/{conversation_id}",
        lambda w, a: (w.url(f"/conversations/{conversation_of(w, a)}"), None),
        EVERY_MEMBER,
        204,
        "manage_own_conversations",
        g_delete_conversation,
        prepare=seed_conversations,
    ),
]


@pytest.fixture(scope="module")
def app(settings):
    # The fake provider answers the key check without a network call.
    keys = {"ai_providers": [AI_PROVIDER], "ai_key_encryption_keys": [SecretStr(DEV_AI_KEY)]}
    return create_app(settings.model_copy(update=keys))


@pytest.mark.parametrize("actor", ACTORS)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name.replace(" ", "-"))
async def test_endpoint(case: Case, actor: str, settings, pg, app):
    async with build_world(settings, pg, app) as world:
        case.prepare(world, pg)
        path, body = case.request(world, actor)
        before, audit_from = snapshot(pg, world), last_audit_id(pg)
        sent = {"files": body} if case.multipart else {"json": body}
        response = await world.clients[actor].request(case.method, path, **sent)
        changes = diff(before, snapshot(pg, world))
        audit = audit_rows(pg, world.ws, audit_from)

        allowed = actor in case.allowed
        expected = case.status if allowed else 401 if actor == "anon" else 404
        assert response.status_code == expected, response.text
        payload = response.json() if response.content else {}
        if allowed:
            case.ground(Ctx(world, actor, payload, changes, audit, pg))
        else:  # a refusal looks like absence and changes nothing (R29)
            assert payload == (NOT_FOUND if expected == 404 else {"detail": "Sign in required."})
            unchanged(Ctx(world, actor, payload, changes, audit, pg))

        if actor in MEMBERS and (allowed and case.audited_when_allowed(actor) or not allowed):
            # Purge receipts are the system's own rows; grounds check them (KTD8).
            ((row,),) = [[r for r in audit if r["channel"] != "system"]]
            assert (str(row["actor_user_id"]), row["action"], row["channel"]) == (
                world.users[actor],
                case.action,
                "ui",
            )
            assert row["outcome"] == ("allow" if allowed else "deny"), row
            assert row["rule_id"], row  # every decision names the rule that made it (R27)
        else:  # outsiders and anonymous callers never write into the workspace trail
            assert audit == [], audit


async def test_another_persons_token_cannot_be_revoked(settings, pg, app):
    """Revoking someone else's token looks like a missing token and changes nothing."""
    async with build_world(settings, pg, app) as world:
        before = snapshot(pg, world)
        response = await world.clients["admin"].delete(
            world.url(f"/tokens/{world.tokens['author']}")
        )
        assert (response.status_code, response.json()) == (404, NOT_FOUND)
        unchanged(Ctx(world, "admin", {}, diff(before, snapshot(pg, world)), [], pg))


MATRIX_ROUTES = {(c.method, c.template) for c in CASES}
