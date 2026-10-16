"""The benchmark corpus (KTD17): the Acme seed documents plus the U3 hostile files, uploaded
through the API, with gold data written as text anchors and per-identity expectations."""

from dataclasses import dataclass

import httpx

from app import seed
from tests import hostile_files as hostile
from tests.helpers import add_document, approve, create_workspace, join, signed_in

ARCHITECTURE = "Checkout service architecture"
TIMEOUTS = "Runbook: checkout payment timeouts"
INCIDENT = "Incident 2026-09-14: checkout outage"
FAILOVER = "Runbook: payment gateway failover"
CACHE = "Runbook: legacy cache flush"
SCALING = "Draft: checkout scaling notes"
HIDDEN_DOCX, HIDDEN_HTML, COMMENTED_MD = "hidden", "hidden-page", "commented"

# Gold anchors: substrings each serving body must contain after extraction.
ANCHORS = {
    ARCHITECTURE: ["card authorisation, 2s timeout", "three replicas behind the internal"],
    TIMEOUTS: ["enable the payments circuit breaker flag", "every 30 minutes"],
    INCIDENT: ["payments-gateway certificate expired", "retry policy reduced to one attempt"],
    FAILOVER: ["Switch the gateway endpoint flag to the secondary region"],
    HIDDEN_DOCX: ["Visible intro.", "Visible end."],
    HIDDEN_HTML: ["Visible one.", "Visible two."],
    COMMENTED_MD: ["# Runbook", "Visible.", "More."],
}
# Hidden-layer strings from the hostile files: never in a body, chunk or search result.
HIDDEN = [
    "COMMENT-SECRET",
    "INSERTED-SECRET",
    "DELETED-SECRET",
    "VANISH-SECRET",
    "WHITE-SECRET",
    "TEXTBOX-SECRET",
    "HIDDEN-DIV",
    "SCRIPT_SECRET",
    "STYLE-HIDDEN",
]

# Which serving documents each identity may retrieve, from the seed's sharing (written by hand).
SEED_DOCS = {ARCHITECTURE, TIMEOUTS, INCIDENT, FAILOVER}
READABLE = {
    "owner": SEED_DOCS | {HIDDEN_DOCX, HIDDEN_HTML, COMMENTED_MD},
    "mei": {ARCHITECTURE, TIMEOUTS},
    "sam": {ARCHITECTURE, INCIDENT, FAILOVER},
    "outsider": set(),
}
# Keyword questions and the document that answers each.
QUESTIONS = [
    ("payments-gateway timeout", ARCHITECTURE),
    ("circuit breaker", TIMEOUTS),
    ("certificate expired", INCIDENT),
    ("secondary region", FAILOVER),
    ("legacy cache", CACHE),  # withdrawn: no identity may get it
    ("Black Friday", SCALING),  # a draft: no identity may get it
]
ENTITIES = ["payments-gateway", "checkout", "checkout-api"]
# Gold mentions: the readable documents whose text names each entity.
MENTIONS = {
    "payments-gateway": {ARCHITECTURE, TIMEOUTS, INCIDENT},
    "checkout": {ARCHITECTURE, TIMEOUTS, INCIDENT},
    "checkout-api": set(),
}


@dataclass
class Corpus:
    workspace_id: str
    clients: dict[str, httpx.AsyncClient]
    titles: dict[str, str]  # version_id -> title, for every version ever served
    entity_ids: dict[str, str]


async def _upload_and_approve(owner, ws: str, name: str, data: bytes) -> dict:
    url = f"/api/workspaces/{ws}/documents"
    response = await owner.post(f"{url}/upload", files={"file": (name, data)})
    response.raise_for_status()
    doc = response.json()
    review = (await owner.get(f"{url}/{doc['id']}/review")).json()
    approved = await owner.post(
        f"{url}/{doc['id']}/approve",
        json={
            "version_id": doc["pending_version_id"],
            "acknowledged_receipt": review["receipt"]["sha256"],
        },
    )
    approved.raise_for_status()
    return doc


async def build(settings, stack) -> Corpus:
    """Build the Acme world in a fresh workspace; `stack` is an AsyncExitStack for the clients."""
    clients = {
        who: await stack.enter_async_context(signed_in(settings))
        for who in ("owner", "mei", "sam", "outsider")
    }
    owner = clients["owner"]
    ws = await create_workspace(owner, "Acme Engineering")
    base = f"/api/workspaces/{ws}"
    groups = {}
    for who, group in (("mei", "Platform"), ("sam", "Payments")):
        await join(owner, ws, clients[who])
        groups[group] = (await owner.post(f"{base}/groups", json={"name": group})).json()["id"]
        await owner.put(f"{base}/groups/{groups[group]}/members/{clients[who].user_id}")
    await join(owner, ws, clients["outsider"])

    titles = {}

    async def served(author: str, title: str, body: str, *share: str) -> dict:
        doc = await add_document(clients[author], ws, title=title, body=body)
        await approve(owner, ws, doc)
        for group in share:
            await owner.put(f"{base}/documents/{doc['id']}/shares/{groups[group]}")
        titles[doc["pending_version_id"]] = title
        return doc

    await served("owner", ARCHITECTURE, seed.ARCHITECTURE, "Platform", "Payments")
    await served("mei", TIMEOUTS, seed.TIMEOUTS, "Platform")
    await served("owner", INCIDENT, seed.INCIDENT, "Payments")
    failover = await served("sam", FAILOVER, seed.FAILOVER_V1, "Payments")
    edit = {"title": FAILOVER, "body": seed.FAILOVER_V2}
    (await clients["sam"].put(f"{base}/documents/{failover['id']}", json=edit)).raise_for_status()
    cache = await served("owner", CACHE, seed.CACHE)
    (await owner.post(f"{base}/documents/{cache['id']}/withdraw")).raise_for_status()
    await add_document(clients["mei"], ws, title=SCALING, body=seed.SCALING)

    for title, name, data in (
        (HIDDEN_DOCX, "hidden.docx", hostile.hidden_docx()),
        (HIDDEN_HTML, "hidden-page.html", hostile.HIDDEN_HTML),
        (COMMENTED_MD, "commented.md", hostile.MARKDOWN_WITH_COMMENT),
    ):
        doc = await _upload_and_approve(owner, ws, name, data)
        titles[doc["pending_version_id"]] = title

    entity_ids = {}
    for name in ENTITIES:
        response = await owner.post(f"{base}/entities", json={"name": name, "aliases": []})
        response.raise_for_status()
        entity_ids[name] = response.json()["id"]
    return Corpus(ws, clients, titles, entity_ids)
