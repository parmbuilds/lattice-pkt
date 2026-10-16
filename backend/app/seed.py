"""Demo data for the incident-support scenario. Run with `python -m app.seed`.

Seeds through the real API in-process, as the runtime role, so every seeded state has its
audit rows. Safe to re-run: it stops if the demo owner already exists.
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings
from app.main import create_app

DEMO_PASSWORD = "lattice-demo-password"  # noqa: S105 - published local demo credential


@dataclass(frozen=True)
class Person:
    name: str
    email: str
    role: str


OWNER = Person("Olivia Owner", "olivia@lattice.demo", "owner")
ADMIN = Person("Arjun Admin", "arjun@lattice.demo", "admin")
MEI = Person("Mei Engineer", "mei@lattice.demo", "member")
SAM = Person("Sam Contractor", "sam@lattice.demo", "member")
PEOPLE = (OWNER, ADMIN, MEI, SAM)
GROUPS = {"Platform": [MEI], "Payments": [SAM]}
ENTITIES = ("payments-gateway", "checkout", "checkout-api")

ARCHITECTURE = """Checkout service architecture

The checkout service accepts orders from the web store and calls three dependencies:
- payments-gateway (card authorisation, 2s timeout, retried once)
- inventory-service (stock reservation)
- notifications-service (order confirmation email, asynchronous)

Checkout runs three replicas behind the internal load balancer. Its database is the orders
PostgreSQL cluster. Dashboards: Checkout overview, Payments latency."""

TIMEOUTS = """Runbook: checkout payment timeouts

Symptoms: checkout error rate above 2 percent with payments-gateway timeouts in the logs.
1. Check the Payments latency dashboard for gateway p95 above 1.5s.
2. If the gateway is degraded, enable the payments circuit breaker flag.
3. Restart checkout replicas one at a time only if connection pools are exhausted.
4. Post an update in the incident channel every 30 minutes."""

INCIDENT = """Incident 2026-09-14: checkout outage

Impact: 41 minutes of failed checkouts for card payments.
Cause: payments-gateway certificate expired; checkout retried every request twice, doubling load.
Resolution: certificate renewed; retry policy reduced to one attempt.
Follow-ups: certificate expiry alerting; load test the retry policy."""

FAILOVER_V1 = """Runbook: payment gateway failover

1. Confirm the primary gateway health check is failing for 5 minutes.
2. Switch the gateway endpoint flag to the secondary region.
3. Watch authorisation success rate return above 98 percent."""

FAILOVER_V2 = (
    FAILOVER_V1
    + """
4. Notify the finance team that settlement will run from the secondary region."""
)

CACHE = """Runbook: legacy cache flush

Flush the legacy product cache with the admin console when prices look stale.
This cache was retired in August; this runbook no longer applies."""

SCALING = """Draft: checkout scaling notes

Black Friday load test results and the proposed replica count for checkout."""

REFUNDS = """Runbook: refund retries

Retry failed refunds by re-running the refund job for the affected orders."""


async def _person(app, settings: Settings, who: Person) -> httpx.AsyncClient:
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=settings.public_origin,
        headers={"Origin": settings.public_origin},
    )
    await client.post(
        "/api/auth/signup", json={"email": who.email, "password": DEMO_PASSWORD, "name": who.name}
    )
    signed = await client.post(
        "/api/auth/signin", json={"email": who.email, "password": DEMO_PASSWORD}
    )
    signed.raise_for_status()
    client.headers["X-CSRF-Token"] = signed.json()["csrf_token"]
    return client


async def _ok(response: httpx.Response) -> dict:
    response.raise_for_status()
    return response.json() if response.content else {}


async def seed(settings: Settings) -> dict | None:
    """Create the demo workspace; returns its summary, or None if already seeded."""
    # Derive in each request: the seed never runs the lifespan worker (KTD3).
    app = create_app(settings.model_copy(update={"derive_inline": True}))
    clients: dict[str, httpx.AsyncClient] = {}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=settings.public_origin,
            headers={"Origin": settings.public_origin},
        ) as probe:
            existing = await probe.post(
                "/api/auth/signin", json={"email": OWNER.email, "password": DEMO_PASSWORD}
            )
        if existing.status_code == 200:
            return None

        for who in PEOPLE:
            clients[who.email] = await _person(app, settings, who)
        owner = clients[OWNER.email]
        ws = (await _ok(await owner.post("/api/workspaces", json={"name": "Acme Engineering"})))[
            "id"
        ]
        base = f"/api/workspaces/{ws}"

        for who in (ADMIN, MEI, SAM):
            link = await _ok(await owner.post(f"{base}/invites", json={"role": who.role}))
            token = link["invite_url"].split("#", 1)[1]
            await _ok(await clients[who.email].post("/api/invites/accept", json={"token": token}))

        groups = {}
        for name, members in GROUPS.items():
            groups[name] = (await _ok(await owner.post(f"{base}/groups", json={"name": name})))[
                "id"
            ]
            for who in members:
                me = await _ok(await clients[who.email].get("/api/auth/me"))
                await _ok(
                    await owner.put(f"{base}/groups/{groups[name]}/members/{me['user']['id']}")
                )

        async def document(author: Person, title: str, body: str) -> dict:
            return await _ok(
                await clients[author.email].post(
                    f"{base}/documents", json={"title": title, "body": body}
                )
            )

        async def approve(doc: dict) -> None:
            await _ok(
                await clients[ADMIN.email].post(
                    f"{base}/documents/{doc['id']}/approve",
                    json={"version_id": doc["pending_version_id"]},
                )
            )

        async def share(doc: dict, *names: str) -> None:
            for name in names:
                await _ok(await owner.put(f"{base}/documents/{doc['id']}/shares/{groups[name]}"))

        architecture = await document(OWNER, "Checkout service architecture", ARCHITECTURE)
        await approve(architecture)
        await share(architecture, "Platform", "Payments")

        timeouts = await document(MEI, "Runbook: checkout payment timeouts", TIMEOUTS)
        await approve(timeouts)
        await share(timeouts, "Platform")

        incident = await document(ADMIN, "Incident 2026-09-14: checkout outage", INCIDENT)
        await approve(incident)
        await share(incident, "Payments")

        failover = await document(SAM, "Runbook: payment gateway failover", FAILOVER_V1)
        await approve(failover)
        await share(failover, "Payments")
        # An edit waiting for review: v1 keeps serving until v2 is approved.
        await _ok(
            await clients[SAM.email].put(
                f"{base}/documents/{failover['id']}",
                json={"title": "Runbook: payment gateway failover", "body": FAILOVER_V2},
            )
        )

        cache = await document(OWNER, "Runbook: legacy cache flush", CACHE)
        await approve(cache)
        await _ok(await owner.post(f"{base}/documents/{cache['id']}/withdraw"))

        await document(MEI, "Draft: checkout scaling notes", SCALING)

        refunds = await document(SAM, "Runbook: refund retries", REFUNDS)
        await _ok(
            await clients[ADMIN.email].post(
                f"{base}/documents/{refunds['id']}/reject",
                json={
                    "version_id": refunds["pending_version_id"],
                    "reason": "Name the refund job and the order query.",
                },
            )
        )
        for name in ENTITIES:
            await _ok(await owner.post(f"{base}/entities", json={"name": name}))
        return {"workspace_id": ws, "people": [p.email for p in PEOPLE]}
    finally:
        for client in clients.values():
            await client.aclose()
        await app.state.engine.dispose()


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    result = asyncio.run(seed(get_settings()))
    if result is None:
        print("Demo data already present.")
        return
    print("Seeded workspace 'Acme Engineering'. Sign in with any of:")
    for person in PEOPLE:
        print(f"  {person.email:<24} {person.role:<7} password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
