"""Retrieval and lifecycle benchmark (R24, KTD17). Run with `pytest -m benchmark -s`."""

import hashlib
import os
import statistics
import time
from contextlib import AsyncExitStack

import psycopg
import pytest
from sqlalchemy import true

import app.services.search as search_module
from app.derived import DERIVED_TABLES
from tests.benchmark import corpus as gold
from tests.conftest import plain_dsn
from tests.endpoint_world import sql
from tests.helpers import add_document, approve, create_workspace, signed_in

pytestmark = pytest.mark.benchmark

CANARY = b"# Canary\n\nRotate the canary credentials.\n<!-- CANARY-HIDDEN -->\n"


async def _search(client, ws, q, limit=5) -> list[dict]:
    response = await client.get(f"/api/workspaces/{ws}/search", params={"q": q, "limit": limit})
    assert response.status_code == 200, response.text
    return response.json()["results"]


def _bodies(pg) -> dict[str, tuple[str, str]]:
    """Every version's (workspace_id, body): a leak can return another workspace's version."""
    rows = sql(pg, "SELECT id::text, workspace_id::text, body FROM document_versions")
    return {vid: (ws, body) for vid, ws, body in rows}


async def run(settings, pg) -> dict[str, float]:
    async with AsyncExitStack() as stack:
        c = await gold.build(settings, stack)
        ws, bodies = c.workspace_id, _bodies(pg)
        served = {c.titles[v]: bodies[v][1] for v in c.titles}
        anchors = [a in served[t] for t, found in gold.ANCHORS.items() for a in found]
        hidden_hits = sum(
            h in body for h in gold.HIDDEN for owner_ws, body in bodies.values() if owner_ws == ws
        )

        returned = denied = hits = expected = spans = resolved = 0
        for who, client in c.clients.items():
            for query, target in gold.QUESTIONS + [(h, None) for h in gold.HIDDEN]:
                results = await _search(client, ws, query)
                titles = [c.titles.get(r["version_id"]) for r in results]
                returned += len(results)
                denied += sum(t not in gold.READABLE[who] for t in titles)
                hidden_hits += sum(h in r["snippet"] for h in gold.HIDDEN for r in results)
                if target in gold.READABLE[who]:
                    expected += 1
                    hits += target in titles
                for r in results:
                    for s in r["spans"]:
                        piece = bodies[r["version_id"]][1][s["start"] : s["end"]]
                        spans += 1
                        resolved += hashlib.sha256(piece.encode()).hexdigest() == s["sha256"]

        precision = {}
        for who, client in c.clients.items():
            reported = correct = 0
            for name, entity_id in c.entity_ids.items():
                response = await client.get(f"/api/workspaces/{ws}/entities/{entity_id}")
                mentions = response.json()["mentions"] if response.status_code == 200 else []
                for m in mentions:
                    reported += 1
                    correct += m["title"] in gold.MENTIONS[name] & gold.READABLE[who]
            precision[who] = correct / reported if reported else 1.0

        deletion = await _withdraw_canary(c.clients["owner"], ws, pg)

    return {
        "extraction completeness": sum(anchors) / len(anchors),
        "hidden strings found": hidden_hits,
        "allowed-only Recall@5": hits / expected,
        "denied-retrieval rate": denied / returned,
        "citation resolution": resolved / spans,
        "deletion propagation": deletion,
        **{f"edge precision ({who})": p for who, p in precision.items()},
    }


async def _withdraw_canary(owner, ws, pg) -> float:
    """Share of places that no longer hold a withdrawn upload: every derived table, search."""
    url = f"/api/workspaces/{ws}/documents"
    doc = (await owner.post(f"{url}/upload", files={"file": ("canary.md", CANARY)})).json()
    review = (await owner.get(f"{url}/{doc['id']}/review")).json()
    await owner.post(
        f"{url}/{doc['id']}/approve",
        json={
            "version_id": doc["pending_version_id"],
            "acknowledged_receipt": review["receipt"]["sha256"],
        },
    )
    found = await _search(owner, ws, "canary")
    assert doc["id"] in {r["document_id"] for r in found}
    assert (await owner.post(f"{url}/{doc['id']}/withdraw")).status_code == 204
    gone = [
        sql(pg, f"SELECT 1 FROM {table} WHERE document_id = %s", doc["id"]) == []  # noqa: S608
        for table in DERIVED_TABLES
    ]
    gone.append(doc["id"] not in {r["document_id"] for r in await _search(owner, ws, "canary")})
    return sum(gone) / len(gone)


def assert_gates(metrics: dict[str, float]) -> None:
    assert metrics["denied-retrieval rate"] == 0
    assert metrics["citation resolution"] == 1.0
    assert metrics["deletion propagation"] == 1.0


def _table(metrics: dict[str, float]) -> str:
    return "\n".join(f"{name:<32} {value:>8.3f}" for name, value in metrics.items())


async def test_benchmark_gates_hold_on_the_acme_corpus(settings, pg):
    metrics = await run(settings, pg)
    print("\n" + _table(metrics))
    assert_gates(metrics)
    assert metrics["extraction completeness"] == 1.0
    assert metrics["hidden strings found"] == 0
    assert metrics["allowed-only Recall@5"] == 1.0
    assert all(v == 1.0 for k, v in metrics.items() if k.startswith("edge precision"))


async def test_a_leaky_search_breaks_the_denied_retrieval_gate(settings, pg, monkeypatch):
    monkeypatch.setattr(search_module, "read_clause", lambda *args: true())
    metrics = await run(settings, pg)
    assert metrics["denied-retrieval rate"] > 0
    with pytest.raises(AssertionError):
        assert_gates(metrics)


def _percentiles(samples: list[float]) -> str:
    cuts = statistics.quantiles(samples, n=20)
    return f"p50 {cuts[9] * 1000:7.1f} ms   p95 {cuts[18] * 1000:7.1f} ms"


async def _timed(samples: list[float], call) -> None:
    start = time.perf_counter()
    response = await call()
    samples.append(time.perf_counter() - start)
    assert getattr(response, "status_code", 200) == 200, response.text


async def test_latency_report(settings, pg, request):
    """Non-gating: ~50,000 chunks plus one 1,000,000-character document, fake embedder."""
    if "benchmark" not in (request.config.option.markexpr or ""):
        pytest.skip("latency report runs only with -m benchmark")
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner, "Latency")
        words = " ".join(f"word{i % 997} topic{i % 89}" for i in range(150))
        for i in range(20):
            await approve(owner, ws, await add_document(owner, ws, f"Note {i}", words))
        for name in ("payments-gateway", "checkout"):
            await owner.post(f"/api/workspaces/{ws}/entities", json={"name": name})

        large = ("checkout payments-gateway " + words + "\n\n") * (1_000_000 // (len(words) + 28))
        derive = []
        for _ in range(3):
            await _timed(derive, lambda: add_document(owner, ws, "Large", large))
        # shortcut: copies existing chunk rows to reach ~50,000; real text would vary vectors.
        (have,) = sql(pg, "SELECT count(*) FROM chunks WHERE workspace_id = %s", ws)[0]
        with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
            conn.execute(
                "INSERT INTO chunks (workspace_id, document_id, version_id, config_hash, ordinal,"
                ' start, "end", sha256, embedding) SELECT workspace_id, document_id, version_id,'
                ' config_hash, ordinal, start, "end", sha256, embedding FROM chunks, '
                "generate_series(1, %s) WHERE workspace_id = %s",
                (max(1, 50_000 // have - 1), ws),
            )
        (total,) = sql(pg, "SELECT count(*) FROM chunks WHERE workspace_id = %s", ws)[0]

        searches, graph = [], []
        for i in range(int(os.getenv("LATENCY_SAMPLES", "20"))):
            await _timed(
                searches,
                lambda i=i: owner.get(
                    f"/api/workspaces/{ws}/search", params={"q": f"word{i} topic{i}"}
                ),
            )
            await _timed(graph, lambda: owner.get(f"/api/workspaces/{ws}/entities"))
    print(f"\nlatency over {total} chunks (fake embedder)")
    print(f"search           {_percentiles(searches)}")
    print(f"graph list       {_percentiles(graph)}")
    print(f"derive 1M chars  {_percentiles(derive)}  ({len(derive)} runs)")
