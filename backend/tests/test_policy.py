import uuid
from dataclasses import replace

import psycopg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.policy import Action, DocumentState, Subject, decide, readable_versions
from tests import reference_model as ref
from tests.conftest import plain_dsn

WS = uuid.uuid4()
OTHER_WS = uuid.uuid4()
OWNER, ADMIN, AUTHOR, OTHER = (uuid.uuid4() for _ in range(4))
GROUP_G, GROUP_H = uuid.uuid4(), uuid.uuid4()


def subject(user, role, groups=(), channel="ui", workspace=WS):
    return Subject(
        user_id=user,
        workspace_id=workspace,
        role=role,
        group_ids=frozenset(groups),
        channel=channel,
    )


ACTORS = {
    "owner": subject(OWNER, "owner"),
    "admin": subject(ADMIN, "admin"),
    "author": subject(AUTHOR, "member", [GROUP_G]),
    "other": subject(OTHER, "member", [GROUP_H]),
}

# An approved document with a pending draft, authored by AUTHOR, shared with group G.
DOC = DocumentState(
    workspace_id=WS,
    document_id=uuid.uuid4(),
    author_id=AUTHOR,
    has_serving=True,
    has_pending=True,
    withdrawn=False,
    shared_group_ids=frozenset([GROUP_G]),
)

Y, N = True, False
# action, extra kwargs, document, expected for (owner, admin, author, other)
WRITE_MATRIX = [
    (Action.CREATE_DOCUMENT, {}, None, (Y, Y, Y, Y)),
    (Action.EDIT, {}, DOC, (N, N, Y, N)),
    (Action.SHARE, {"target_group_id": GROUP_G}, DOC, (Y, Y, Y, N)),
    (Action.SHARE, {"target_group_id": GROUP_H}, DOC, (Y, Y, N, N)),
    (Action.UNSHARE, {"target_group_id": GROUP_H}, DOC, (Y, Y, Y, N)),
    (Action.APPROVE, {}, DOC, (Y, Y, N, N)),
    (Action.REJECT, {}, DOC, (Y, Y, N, N)),
    (Action.DISCARD, {}, DOC, (N, N, Y, N)),
    (Action.WITHDRAW, {}, DOC, (Y, Y, Y, N)),
    (Action.REASSIGN_AUTHOR, {}, DOC, (Y, Y, N, N)),
    (Action.MANAGE_GROUPS, {}, None, (Y, Y, N, N)),
    (Action.VIEW_ROSTER, {}, None, (Y, Y, N, N)),
    (Action.VIEW_AUDIT, {}, None, (Y, Y, N, N)),
    (Action.INVITE, {"target_role": "member"}, None, (Y, Y, N, N)),
    (Action.INVITE, {"target_role": "admin"}, None, (Y, N, N, N)),
    (Action.REMOVE_MEMBER, {"target_role": "member"}, None, (Y, Y, N, N)),
    (Action.REMOVE_MEMBER, {"target_role": "admin"}, None, (Y, N, N, N)),
    (Action.REMOVE_MEMBER, {"target_role": "owner"}, None, (N, N, N, N)),
    (Action.CHANGE_ROLE, {"target_role": "admin"}, None, (Y, N, N, N)),
    (Action.TRANSFER_OWNERSHIP, {"target_role": "admin"}, None, (Y, N, N, N)),
    (Action.TRANSFER_OWNERSHIP, {"target_role": "member"}, None, (N, N, N, N)),
    (Action.MANAGE_OWN_TOKENS, {}, None, (Y, Y, Y, Y)),
    (Action.REVOKE_INVITE, {}, None, (Y, Y, N, N)),
]


@pytest.mark.parametrize(("action", "kwargs", "document", "expected"), WRITE_MATRIX)
def test_write_actions_follow_the_role_capabilities_table(action, kwargs, document, expected):
    outcomes = tuple(
        decide(ACTORS[name], action, document, **kwargs).allowed
        for name in ("owner", "admin", "author", "other")
    )
    assert outcomes == expected


WITHDRAWN = replace(DOC, withdrawn=True, has_pending=False)
NO_DRAFT = replace(DOC, has_pending=False)


@pytest.mark.parametrize(
    ("action", "document", "actor"),
    [
        (Action.EDIT, WITHDRAWN, "author"),
        (Action.APPROVE, WITHDRAWN, "admin"),
        (Action.APPROVE, NO_DRAFT, "admin"),
        (Action.REJECT, NO_DRAFT, "admin"),
        (Action.DISCARD, NO_DRAFT, "author"),
        (Action.SHARE, WITHDRAWN, "admin"),
        (Action.WITHDRAW, WITHDRAWN, "owner"),
    ],
)
def test_lifecycle_state_blocks_actions_that_no_longer_apply(action, document, actor):
    kwargs = {"target_group_id": GROUP_G} if action is Action.SHARE else {}
    assert decide(ACTORS[actor], action, document, **kwargs).allowed is False


def test_mcp_channel_allows_retrieval_only():
    agent = replace(ACTORS["owner"], channel="mcp")
    serving = replace(DOC, version_is_serving=True)
    assert decide(agent, Action.RETRIEVE, serving).allowed is True
    for action in (Action.APPROVE, Action.SHARE, Action.INVITE, Action.MANAGE_OWN_TOKENS):
        decision = decide(agent, action, DOC, target_group_id=GROUP_G, target_role="member")
        assert decision.allowed is False
        assert decision.rule == "channel.mcp_retrieval_only"


def test_admin_retrieves_an_approved_document_shared_with_no_group():
    """Covers AE1."""
    unshared = replace(DOC, shared_group_ids=frozenset(), version_is_serving=True)
    decision = decide(ACTORS["admin"], Action.RETRIEVE, unshared)
    assert decision.allowed is True
    assert decision.rule == "retrieve.manager"


def test_no_role_retrieves_a_draft_but_author_and_managers_can_view_it():
    """Covers AE2."""
    draft = replace(DOC, has_serving=False, version_is_serving=False, version_is_pending=True)
    for name in ("owner", "admin", "author", "other"):
        assert decide(ACTORS[name], Action.RETRIEVE, draft).allowed is False
    views = {name: decide(ACTORS[name], Action.VIEW, draft).allowed for name in ACTORS}
    assert views == {"owner": True, "admin": True, "author": True, "other": False}


def test_a_direct_user_grant_gives_read_access_until_removed():
    granted = replace(
        DOC,
        shared_group_ids=frozenset(),
        shared_user_ids=frozenset([OTHER]),
        version_is_serving=True,
    )
    assert decide(ACTORS["other"], Action.RETRIEVE, granted).rule == "retrieve.shared"
    revoked = replace(granted, shared_user_ids=frozenset())
    assert decide(ACTORS["other"], Action.RETRIEVE, revoked).allowed is False


def test_a_shared_reader_sees_only_the_serving_version():
    reader = subject(OTHER, "member", [GROUP_G])
    serving = replace(DOC, version_is_serving=True)
    pending = replace(DOC, version_is_pending=True)
    assert decide(reader, Action.VIEW, serving).allowed is True
    assert decide(reader, Action.VIEW, pending).allowed is False
    withdrawn = replace(serving, withdrawn=True)
    assert decide(reader, Action.VIEW, withdrawn).allowed is False
    assert decide(ACTORS["author"], Action.VIEW, withdrawn).allowed is True


def test_non_members_are_denied_with_one_rule_whatever_the_resource():
    outsider = subject(uuid.uuid4(), None)
    other_ws_doc = replace(DOC, workspace_id=OTHER_WS, version_is_serving=True)
    member_elsewhere = replace(ACTORS["owner"])
    rules = {
        decide(outsider, Action.RETRIEVE, replace(DOC, version_is_serving=True)).rule,
        decide(outsider, Action.VIEW_AUDIT).rule,
        decide(member_elsewhere, Action.RETRIEVE, other_ws_doc).rule,
    }
    assert rules == {"workspace.not_member"}


def test_a_denied_decision_records_its_rule_and_inputs():
    decision = decide(ACTORS["other"], Action.APPROVE, DOC)
    assert decision.allowed is False
    assert decision.rule == "approve.not_permitted"
    assert decision.inputs["role"] == "member"
    assert decision.inputs["channel"] == "ui"
    assert decision.inputs["document_id"] == str(DOC.document_id)


# --- SQL read predicates against a fixed seeded state -------------------------------------


def _seed(conn) -> tuple[ref.World, dict[str, Subject]]:
    """Every role, grant type and lifecycle state across two workspaces."""
    world = ref.World()

    def user(name):
        return conn.execute(
            "INSERT INTO users (email, display_name, password_hash) "
            "VALUES (%s, %s, 'x') RETURNING id",
            (f"{name}-{uuid.uuid4()}@example.test", name),
        ).fetchone()[0]

    def workspace():
        return conn.execute("INSERT INTO workspaces (name) VALUES ('w') RETURNING id").fetchone()[0]

    def member(ws, uid, role):
        conn.execute(
            "INSERT INTO memberships (workspace_id, user_id, role) VALUES (%s, %s, %s)",
            (ws, uid, role),
        )
        world.roles[(ws, uid)] = role

    def group(ws, members):
        gid = conn.execute(
            "INSERT INTO groups (workspace_id, name) VALUES (%s, %s) RETURNING id",
            (ws, f"g-{uuid.uuid4()}"),
        ).fetchone()[0]
        for uid in members:
            conn.execute(
                "INSERT INTO group_members (workspace_id, group_id, user_id) VALUES (%s, %s, %s)",
                (ws, gid, uid),
            )
        world.groups[gid] = (ws, set(members))
        return gid

    def document(ws, author, states, withdrawn=False, groups=(), users=()):
        """states: list of 'serving' / 'pending' / 'old' for each version created."""
        doc = conn.execute(
            "INSERT INTO documents (workspace_id, author_id) VALUES (%s, %s) RETURNING id",
            (ws, author),
        ).fetchone()[0]
        versions, serving, pending = [], None, None
        for state in states:
            vid = conn.execute(
                "INSERT INTO document_versions (workspace_id, document_id, title, body, created_by)"
                " VALUES (%s, %s, 'title', 'body', %s) RETURNING id",
                (ws, doc, author),
            ).fetchone()[0]
            versions.append(vid)
            serving = vid if state == "serving" else serving
            pending = vid if state == "pending" else pending
        conn.execute(
            "UPDATE documents SET serving_version_id = %s, pending_version_id = %s, "
            "withdrawn_at = CASE WHEN %s THEN now() END WHERE id = %s",
            (serving, pending, withdrawn, doc),
        )
        for gid in groups:
            conn.execute(
                "INSERT INTO document_grants (workspace_id, document_id, group_id) "
                "VALUES (%s, %s, %s)",
                (ws, doc, gid),
            )
        for uid in users:
            conn.execute(
                "INSERT INTO document_grants (workspace_id, document_id, user_id) "
                "VALUES (%s, %s, %s)",
                (ws, doc, uid),
            )
        world.documents[doc] = {
            "workspace": ws,
            "author": author,
            "serving": serving,
            "withdrawn": withdrawn,
            "versions": set(versions),
            "group_grants": set(groups),
            "user_grants": set(users),
        }

    ws_a, ws_b = workspace(), workspace()
    owner, admin, author, reader, direct, outsider = (
        user(n) for n in ("owner", "admin", "author", "reader", "direct", "outsider")
    )
    member(ws_a, owner, "owner")
    member(ws_a, admin, "admin")
    for uid in (author, reader, direct):
        member(ws_a, uid, "member")
    member(ws_b, outsider, "owner")
    member(ws_b, reader, "member")  # a member of both workspaces
    g = group(ws_a, [author, reader])
    group(ws_a, [direct])
    document(ws_a, author, ["old", "serving", "pending"], groups=[g])
    document(ws_a, author, ["pending"], groups=[g])
    document(ws_a, author, ["serving"], withdrawn=True, groups=[g])
    document(ws_a, admin, ["serving"])
    document(ws_a, author, ["serving"], users=[direct])
    document(ws_b, outsider, ["serving"])

    subjects = {}
    for ws in (ws_a, ws_b):
        for (rws, uid), role in world.roles.items():
            if rws != ws:
                continue
            groups_of = frozenset(
                gid for gid, (gws, members) in world.groups.items() if gws == ws and uid in members
            )
            subjects[f"{ws}:{uid}"] = Subject(
                user_id=uid, workspace_id=ws, role=role, group_ids=groups_of, channel="ui"
            )
    # Someone with no membership in workspace A at all.
    subjects["outsider-in-a"] = Subject(
        user_id=outsider, workspace_id=ws_a, role=None, group_ids=frozenset(), channel="ui"
    )
    return world, subjects


async def test_sql_read_predicates_match_the_reference_model(pg):
    with psycopg.connect(plain_dsn(pg["app"])) as conn:
        world, subjects = _seed(conn)
        conn.commit()
    engine = create_async_engine(pg["app"])
    try:
        async with engine.connect() as conn:
            for subj in subjects.values():
                for action, oracle in (
                    (Action.RETRIEVE, ref.retrievable_versions),
                    (Action.VIEW, ref.viewable_versions),
                ):
                    rows = await conn.execute(readable_versions(subj, action))
                    got = {row[0] for row in rows}
                    want = oracle(world, subj.workspace_id, subj.user_id)
                    # Only compare within this seed; other tests may add rows.
                    seeded = {v for d in world.documents.values() for v in d["versions"]}
                    assert got & seeded == want, (subj, action)
    finally:
        await engine.dispose()
