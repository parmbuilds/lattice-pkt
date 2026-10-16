"""U12: R26 across the finished services (KTD3).

A Hypothesis state machine drives the real HTTP API through workspace, membership, group, sharing
and lifecycle changes. After every step it checks that, for every person in every workspace, the
reference model, the SQL read predicates, the single-item decision, UI search and the MCP search
service all agree on what that person may read.
"""

import asyncio
import hashlib
import uuid
from dataclasses import replace
from unittest.mock import patch

import httpx
import psycopg
import pytest
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)
from sqlalchemy import and_, select

import app.policy as policy_module
import app.services.search as search_module
from app.auth import load_subject
from app.main import create_app
from app.models import DocumentVersion
from app.policy import Action, DocumentState, decide, readable_versions
from app.services import graph
from app.services.documents import DERIVATIONS_NOT_READY
from app.services.search import search
from tests import reference_model as ref
from tests.conftest import plain_dsn
from tests.helpers import PASSWORD
from tests.test_search import _near

TERM = "latticeterm"  # every document body contains it, so search can see every retrievable one
NEAR_TERM = _near(TERM)  # in no body, but the fake embedder puts it next to every one
MEMBER_NAMES = ("admin_a", "m1", "m2", "m3")


class PolicyMachine(RuleBasedStateMachine):
    """Two workspaces per example: A (owner, admin, three members) and B (owner, m2)."""

    def __init__(self, settings, pg):
        super().__init__()
        self.settings = settings
        self.pg = pg
        self.loop = asyncio.new_event_loop()
        self.app = create_app(settings)
        self.world = ref.World()
        self.clients: dict[str, httpx.AsyncClient] = {}
        self.user_ids: dict[str, uuid.UUID] = {}
        self.docs: dict[uuid.UUID, dict] = {}  # document id -> {"ws", "author", "pending"}
        self.ws: dict[str, uuid.UUID] = {}
        # Versions derived at the current config: drafts derive in their request (derive_inline).
        self.ready: set[uuid.UUID] = set()

    # --- plumbing --------------------------------------------------------------------------

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    async def _client(self, name: str) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=self.settings.public_origin,
            headers={"Origin": self.settings.public_origin},
        )
        email = f"{name}-{uuid.uuid4()}@example.test"
        await client.post(
            "/api/auth/signup", json={"email": email, "password": PASSWORD, "name": name}
        )
        signed = await client.post("/api/auth/signin", json={"email": email, "password": PASSWORD})
        client.headers["X-CSRF-Token"] = signed.json()["csrf_token"]
        me = (await client.get("/api/auth/me")).json()
        self.user_ids[name] = uuid.UUID(me["user"]["id"])
        return client

    async def _call(self, name, method, url, **kwargs) -> httpx.Response:
        response = await self.clients[name].request(method, url, **kwargs)
        assert response.status_code < 500, (method, url, response.text)
        return response

    async def _join(self, owner: str, ws: str, name: str, role: str = "member") -> None:
        created = await self._call(
            owner, "POST", f"/api/workspaces/{self.ws[ws]}/invites", json={"role": role}
        )
        token = created.json()["invite_url"].split("#", 1)[1]
        accepted = await self._call(name, "POST", "/api/invites/accept", json={"token": token})
        assert accepted.status_code == 200, accepted.text
        self.world.roles[(self.ws[ws], self.user_ids[name])] = role

    def teardown(self):
        async def close():
            for client in self.clients.values():
                await client.aclose()
            await self.app.state.engine.dispose()

        self.run(close())
        self.loop.close()

    # --- setup -----------------------------------------------------------------------------

    @initialize()
    def setup(self):
        async def go():
            for name in ("owner_a", "owner_b", *MEMBER_NAMES):
                self.clients[name] = await self._client(name)
            for ws, owner in (("A", "owner_a"), ("B", "owner_b")):
                created = await self._call(owner, "POST", "/api/workspaces", json={"name": ws})
                self.ws[ws] = uuid.UUID(created.json()["id"])
                self.world.roles[(self.ws[ws], self.user_ids[owner])] = "owner"
            await self._join("owner_a", "A", "admin_a", "admin")
            for name in ("m1", "m2", "m3"):
                await self._join("owner_a", "A", name)
            await self._join("owner_b", "B", "m2")
            # Two groups from the start so sharing and membership rules apply immediately.
            for name, members in (("red", ["m1"]), ("blue", ["m2", "m3"])):
                created = await self._call(
                    "owner_a",
                    "POST",
                    f"/api/workspaces/{self.ws['A']}/groups",
                    json={"name": name},
                )
                group = uuid.UUID(created.json()["id"])
                self.world.groups[group] = (self.ws["A"], set())
                for member in members:
                    await self._call(
                        "owner_a",
                        "PUT",
                        f"/api/workspaces/{self.ws['A']}/groups/{group}/members/"
                        f"{self.user_ids[member]}",
                    )
                    self.world.groups[group][1].add(self.user_ids[member])
            # Every body contains TERM, so the graph mentions each version a person may read.
            for ws, owner in (("A", "owner_a"), ("B", "owner_b")):
                created = await self._call(
                    owner, "POST", f"/api/workspaces/{self.ws[ws]}/entities", json={"name": TERM}
                )
                self.world.entities[uuid.UUID(created.json()["id"])] = (self.ws[ws], [TERM])

        self.run(go())

    def _ws_of(self, name: str) -> str:
        return "B" if name == "owner_b" else "A"

    def _member_names(self, ws: str) -> list[str]:
        return [n for n, uid in self.user_ids.items() if (self.ws[ws], uid) in self.world.roles]

    # --- documents -------------------------------------------------------------------------

    @rule(author=st.sampled_from(["owner_a", "admin_a", "m1", "m2", "m3", "owner_b"]))
    def create_document(self, author):
        ws = self._ws_of(author)
        if (self.ws[ws], self.user_ids[author]) not in self.world.roles or len(self.docs) >= 8:
            return
        body = f"{TERM} {uuid.uuid4().hex}"
        created = self.run(
            self._call(
                author,
                "POST",
                f"/api/workspaces/{self.ws[ws]}/documents",
                json={"title": f"Doc by {author}", "body": body},
            )
        ).json()
        doc_id, version = uuid.UUID(created["id"]), uuid.UUID(created["pending_version_id"])
        self.world.bodies[version] = body
        self.docs[doc_id] = {"ws": ws, "pending": version}
        self.ready.add(version)
        self.world.documents[doc_id] = {
            "workspace": self.ws[ws],
            "author": self.user_ids[author],
            "serving": None,
            "withdrawn": False,
            "versions": {version},
            "group_grants": set(),
            "user_grants": set(),
        }

    def _pick(self, data, predicate=lambda d: True):
        candidates = sorted(d for d in self.docs if predicate(self.world.documents[d]))
        return data.draw(st.sampled_from(candidates)) if candidates else None

    def _owner_of(self, doc_id) -> str:
        return "owner_b" if self.docs[doc_id]["ws"] == "B" else "owner_a"

    def _url(self, doc_id, tail="") -> str:
        return f"/api/workspaces/{self.ws[self.docs[doc_id]['ws']]}/documents/{doc_id}{tail}"

    @precondition(lambda self: self.docs)
    @rule(data=st.data())
    def edit_document(self, data):
        doc_id = self._pick(data, lambda d: not d["withdrawn"])
        if doc_id is None:
            return
        author = next(
            n for n, uid in self.user_ids.items() if uid == self.world.documents[doc_id]["author"]
        )
        if (self.world.documents[doc_id]["workspace"], self.user_ids[author]) not in (
            self.world.roles
        ):
            return  # the author left; only a reassigned author could edit
        body = f"{TERM} {uuid.uuid4().hex}"
        response = self.run(
            self._call(author, "PUT", self._url(doc_id), json={"title": "Edited", "body": body})
        )
        assert response.status_code == 200, response.text
        version = uuid.UUID(response.json()["pending_version_id"])
        self.world.bodies[version] = body
        self.docs[doc_id]["pending"] = version
        self.ready.add(version)
        self.world.documents[doc_id]["versions"].add(version)

    @precondition(lambda self: self.docs)
    @rule(data=st.data(), decision=st.sampled_from(["approve", "reject"]))
    def decide_draft(self, data, decision):
        doc_id = self._pick(data, lambda d: not d["withdrawn"])
        if doc_id is None or self.docs[doc_id]["pending"] is None:
            return
        pending = self.docs[doc_id]["pending"]
        response = self.run(
            self._call(
                self._owner_of(doc_id),
                "POST",
                self._url(doc_id, f"/{decision}"),
                json={"version_id": str(pending)},
            )
        )
        if decision == "approve" and pending not in self.ready:
            # Approve waits for a derivation at the current config, and changes nothing (R10).
            assert (response.status_code, response.json()) == (
                409,
                {"detail": DERIVATIONS_NOT_READY.detail},
            ), response.text
            return
        assert response.status_code in (200, 204), response.text
        if decision == "approve":
            self.world.documents[doc_id]["serving"] = pending
        self.docs[doc_id]["pending"] = None

    @rule()
    def change_pipeline_config(self):
        """A new model or chunker version: every existing derivation is out of date (R14)."""
        self.app.state.embedder.config_identity = f"fake-{uuid.uuid4()}"
        self.ready.clear()

    @precondition(lambda self: self.docs)
    @rule(data=st.data())
    def withdraw(self, data):
        doc_id = self._pick(data, lambda d: not d["withdrawn"])
        if doc_id is None:
            return
        response = self.run(
            self._call(self._owner_of(doc_id), "POST", self._url(doc_id, "/withdraw"))
        )
        assert response.status_code == 204, response.text
        self.world.documents[doc_id]["withdrawn"] = True
        self.docs[doc_id]["pending"] = None

    # --- groups and sharing (workspace A) ---------------------------------------------------

    @rule(name=st.sampled_from(["red", "blue", "green"]))
    def create_group(self, name):
        if len(self.world.groups) >= 4:
            return
        response = self.run(
            self._call(
                "owner_a",
                "POST",
                f"/api/workspaces/{self.ws['A']}/groups",
                json={"name": f"{name}-{uuid.uuid4().hex[:6]}"},
            )
        )
        self.world.groups[uuid.UUID(response.json()["id"])] = (self.ws["A"], set())

    def _pick_group(self, data):
        groups = sorted(self.world.groups)
        return data.draw(st.sampled_from(groups)) if groups else None

    @rule(data=st.data(), name=st.sampled_from(MEMBER_NAMES), add=st.booleans())
    def change_group_membership(self, data, name, add):
        group = self._pick_group(data)
        uid = self.user_ids[name]
        if group is None or (self.ws["A"], uid) not in self.world.roles:
            return
        method = "PUT" if add else "DELETE"
        self.run(
            self._call(
                "owner_a", method, f"/api/workspaces/{self.ws['A']}/groups/{group}/members/{uid}"
            )
        )
        members = self.world.groups[group][1]
        members.add(uid) if add else members.discard(uid)

    @precondition(lambda self: self.docs)
    @rule(data=st.data(), share=st.booleans())
    def change_sharing(self, data, share):
        doc_id = self._pick(data, lambda d: d["workspace"] == self.ws["A"])
        group = self._pick_group(data)
        if doc_id is None or group is None:
            return
        doc = self.world.documents[doc_id]
        if share and doc["withdrawn"]:
            return  # sharing a withdrawn document is refused (policy) and changes nothing
        method = "PUT" if share else "DELETE"
        response = self.run(self._call("owner_a", method, self._url(doc_id, f"/shares/{group}")))
        assert response.status_code == 204, response.text
        doc["group_grants"].add(group) if share else doc["group_grants"].discard(group)

    @precondition(lambda self: self.docs)
    @rule(data=st.data(), name=st.sampled_from(MEMBER_NAMES))
    def grant_directly(self, data, name):
        """Direct user grants have no API; tests create them (Assumptions)."""
        doc_id = self._pick(data, lambda d: d["workspace"] == self.ws["A"])
        uid = self.user_ids[name]
        if doc_id is None or (self.ws["A"], uid) not in self.world.roles:
            return
        with psycopg.connect(plain_dsn(self.pg["app"])) as conn:
            conn.execute(
                "INSERT INTO document_grants (workspace_id, document_id, user_id) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (self.ws["A"], doc_id, uid),
            )
        self.world.documents[doc_id]["user_grants"].add(uid)

    # --- roles and membership (workspace A) -------------------------------------------------

    @rule(name=st.sampled_from(MEMBER_NAMES), role=st.sampled_from(["admin", "member"]))
    def change_role(self, name, role):
        uid = self.user_ids[name]
        if (self.ws["A"], uid) not in self.world.roles:
            return
        self.run(
            self._call(
                "owner_a",
                "PATCH",
                f"/api/workspaces/{self.ws['A']}/members/{uid}",
                json={"role": role},
            )
        )
        self.world.roles[(self.ws["A"], uid)] = role

    @rule(name=st.sampled_from(MEMBER_NAMES))
    def remove_or_rejoin(self, name):
        uid = self.user_ids[name]
        key = (self.ws["A"], uid)
        if key in self.world.roles:
            response = self.run(
                self._call("owner_a", "DELETE", f"/api/workspaces/{self.ws['A']}/members/{uid}")
            )
            assert response.status_code == 204, response.text
            del self.world.roles[key]
            for _, members in self.world.groups.values():
                members.discard(uid)  # group memberships go with the membership
            for doc in self.world.documents.values():
                if doc["workspace"] == self.ws["A"]:
                    doc["user_grants"].discard(uid)  # so do direct grants
        else:
            self.run(self._join("owner_a", "A", name))

    # --- the property ----------------------------------------------------------------------

    @invariant()
    def every_read_path_agrees_with_the_reference_model(self):
        if not self.ws:
            return
        self.run(self._check())

    async def _check(self):
        seeded = {v for d in self.world.documents.values() for v in d["versions"]}
        async with self.app.state.sessionmaker() as db:
            for ws_name, ws in self.ws.items():
                for name, uid in self.user_ids.items():
                    subject = await load_subject(db, uid, ws)
                    want_retrieve = ref.retrievable_versions(self.world, ws, uid)
                    want_view = ref.viewable_versions(self.world, ws, uid)
                    label = (ws_name, name)

                    got = {v for v in await db.scalars(readable_versions(subject, Action.RETRIEVE))}
                    assert got & seeded == want_retrieve, ("sql retrieve", label)
                    got = {v for v in await db.scalars(readable_versions(subject, Action.VIEW))}
                    assert got & seeded == want_view, ("sql view", label)

                    for doc_id, doc in self.world.documents.items():
                        for vid in doc["versions"]:
                            state = _state(doc_id, doc, vid, self.docs[doc_id]["pending"])
                            assert decide(subject, Action.RETRIEVE, state).allowed == (
                                vid in want_retrieve
                            ), ("decide retrieve", label, vid)
                            assert decide(subject, Action.VIEW, state).allowed == (
                                vid in want_view
                            ), ("decide view", label, vid)

                    if subject.role is None:
                        continue
                    ui = await self.clients[name].get(
                        f"/api/workspaces/{ws}/search", params={"q": TERM, "limit": 20}
                    )
                    assert {uuid.UUID(r["version_id"]) for r in ui.json()["results"]} == (
                        want_retrieve
                    ), ("ui search", label)
                    agent = replace(subject, channel="mcp")
                    embedder = self.app.state.embedder
                    via_mcp = await search(db, agent, TERM, 20, embedder, self.settings)
                    assert {uuid.UUID(r["version_id"]) for r in via_mcp} == want_retrieve, (
                        "mcp search",
                        label,
                    )
                    # Meaning only: vectors exist just for versions derived at the current config.
                    via_vector = await search(db, agent, NEAR_TERM, 20, embedder, self.settings)
                    assert {uuid.UUID(r["version_id"]) for r in via_vector} == (
                        want_retrieve & self.ready
                    ), ("vector search", label)
                    await _check_spans(db, via_mcp + via_vector)
                    # The graph sees exactly the mentions in versions this person may read (R22).
                    got_mentions = {
                        (m["entity_id"], m["term"], m["version_id"], m["start"], m["end"])
                        for m in await graph.mentions(db, subject)
                        if m["version_id"] in seeded
                    }
                    assert got_mentions == ref.visible_mentions(self.world, ws, uid), (
                        "graph",
                        label,
                    )


async def _check_spans(db, results) -> None:
    """Every returned span re-slices its approved body to the recorded sha256 (R16)."""
    ids = [uuid.UUID(r["version_id"]) for r in results]
    rows = await db.execute(
        select(DocumentVersion.id, DocumentVersion.body).where(DocumentVersion.id.in_(ids))
    )
    bodies = {str(vid): body for vid, body in rows}
    for r in results:
        for span in r["spans"]:
            piece = bodies[r["version_id"]][span["start"] : span["end"]]
            assert hashlib.sha256(piece.encode()).hexdigest() == span["sha256"], ("span", r)


def _state(doc_id, doc, version_id, pending) -> DocumentState:
    return DocumentState(
        workspace_id=doc["workspace"],
        document_id=doc_id,
        author_id=doc["author"],
        has_serving=doc["serving"] is not None,
        has_pending=pending is not None,
        withdrawn=doc["withdrawn"],
        shared_group_ids=frozenset(doc["group_grants"]),
        shared_user_ids=frozenset(doc["user_grants"]),
        version_is_serving=version_id == doc["serving"],
    )


@pytest.mark.property
def test_every_read_path_agrees_after_any_sequence_of_changes(settings, pg):
    run_state_machine_as_test(lambda: PolicyMachine(settings, pg))


@pytest.mark.property
def test_the_property_catches_a_broken_read_rule(settings, pg, monkeypatch):
    """Remove the 'not withdrawn' term from shared retrieval and the invariant must fail."""
    rules = dict(policy_module.READ_RULES)
    rules[Action.RETRIEVE] = [
        (name, tuple(t for t in terms if t is not policy_module.NOT_WITHDRAWN))
        if name == "retrieve.shared"
        else (name, terms)
        for name, terms in rules[Action.RETRIEVE]
    ]
    monkeypatch.setattr(policy_module, "READ_RULES", rules)
    machine = PolicyMachine(settings, pg)
    try:
        machine.setup()
        machine.create_group("red")
        group = next(iter(machine.world.groups))
        machine.change_group_membership(_fixed(group), "m1", True)
        machine.create_document("owner_a")
        doc = next(iter(machine.docs))
        machine.decide_draft(_fixed(doc), "approve")
        machine.change_sharing(_fixed(doc, group), True)
        machine.every_read_path_agrees_with_the_reference_model()  # still fine
        machine.withdraw(_fixed(doc))
        with pytest.raises(AssertionError):
            machine.every_read_path_agrees_with_the_reference_model()
    finally:
        machine.teardown()


@pytest.mark.property
def test_the_property_catches_a_vector_path_that_skips_the_read_clause(settings, pg, monkeypatch):
    """Drop the read rules from the vector candidates only, and the invariant must fail."""
    vector_candidates = search_module._vector_candidates

    def workspace_only(subject, action, d, v):
        # Keeps the workspace boundary so earlier examples' rows in the shared database stay out.
        return d.c.workspace_id == subject.workspace_id

    def unfiltered(*args):
        with patch.object(search_module, "read_clause", workspace_only):
            return vector_candidates(*args)

    monkeypatch.setattr(search_module, "_vector_candidates", unfiltered)
    machine = PolicyMachine(settings, pg)
    try:
        machine.setup()
        machine.every_read_path_agrees_with_the_reference_model()  # still fine
        machine.create_document("m1")  # a draft nobody may retrieve, but it has vectors
        with pytest.raises(AssertionError):
            machine.every_read_path_agrees_with_the_reference_model()
    finally:
        machine.teardown()


@pytest.mark.property
def test_the_property_catches_a_graph_that_skips_the_read_clause(settings, pg, monkeypatch):
    """Match mentions across the whole workspace instead of readable versions: must fail."""

    def workspace_only(subject, action, d, v):
        return and_(d.c.workspace_id == subject.workspace_id, v.c.document_id == d.c.id)

    monkeypatch.setattr(graph, "read_clause", workspace_only)
    machine = PolicyMachine(settings, pg)
    try:
        machine.setup()
        machine.every_read_path_agrees_with_the_reference_model()  # no documents yet
        machine.create_document("m1")  # a draft nobody may retrieve, but its body names TERM
        with pytest.raises(AssertionError, match="graph"):
            machine.every_read_path_agrees_with_the_reference_model()
    finally:
        machine.teardown()


class _fixed:
    """Stands in for Hypothesis's `data` and returns preset choices in order."""

    def __init__(self, *values):
        self.values = list(values)

    def draw(self, _strategy):
        return self.values.pop(0)
