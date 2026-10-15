"""Real-model semantic gate (R24): `FASTEMBED_CACHE_PATH=... pytest -m benchmark_model -s`."""

from contextlib import AsyncExitStack

import pytest

from app.config import Settings
from app.embedding import make_embedder
from tests.benchmark import corpus as gold
from tests.endpoint_world import sql

pytestmark = pytest.mark.benchmark_model
SHIPPED_FLOOR = Settings.model_fields["vector_distance_floor"].default

# Each question shares no word (or English stem) with its target passage or title.
PARAPHRASES = [
    ("Why could shoppers not complete purchases for most of an hour in September?", gold.INCIDENT),
    ("steps when the card processor responds sluggishly", gold.TIMEOUTS),
    ("move card approvals to the backup location when the main provider is down", gold.FAILOVER),
    ("which downstream systems does the purchase flow rely upon", gold.ARCHITECTURE),
]
OFF_TOPIC = [
    "chocolate cake recipe with vanilla frosting",
    "best hiking trails in the alps",
    "history of the roman empire",
]


async def _recall(client, ws, titles) -> float:
    hits = 0
    for query, target in PARAPHRASES:
        response = await client.get(f"/api/workspaces/{ws}/search", params={"q": query, "limit": 5})
        hits += target in [titles.get(r["version_id"]) for r in response.json()["results"]]
    return hits / len(PARAPHRASES)


def _distance(pg, ws, vector, version_ids=None) -> float:
    query = "SELECT min(embedding <=> %s::vector) FROM chunks WHERE workspace_id = %s"
    params = [str(vector), ws]
    if version_ids is not None:
        query += " AND version_id::text = ANY(%s)"
        params.append(version_ids)
    return sql(pg, query, *params)[0][0]


async def test_meaning_beats_keywords_and_off_topic_returns_nothing(settings, pg):
    model = settings.model_copy(update={"embedder": "fastembed"})
    embedder = make_embedder(model)
    async with AsyncExitStack() as stack:
        c = await gold.build(model, stack)
        ws, owner = c.workspace_id, c.clients["owner"]
        by_title = {
            title: [v for v, t in c.titles.items() if t == title] for title in gold.SEED_DOCS
        }
        targets = [
            _distance(pg, ws, await embedder.embed_query(q), by_title[t]) for q, t in PARAPHRASES
        ]
        off = [_distance(pg, ws, await embedder.embed_query(q)) for q in OFF_TOPIC]
        floor = round((max(targets) + min(off)) / 2, 3)
        print(f"\nparaphrase target distances {[round(d, 3) for d in targets]}")
        print(f"off-topic nearest distances {[round(d, 3) for d in off]}")
        print(f"chosen vector_distance_floor {floor} (shipped {SHIPPED_FLOOR})")

        for app_floor in (floor, 0.0):  # 0.0 lets no chunk through: keyword-only
            owner._transport.app.state.settings = model.model_copy(
                update={"vector_distance_floor": app_floor}
            )
            if app_floor:
                hybrid = await _recall(owner, ws, c.titles)
                for q in OFF_TOPIC:
                    found = await owner.get(f"/api/workspaces/{ws}/search", params={"q": q})
                    assert found.json()["results"] == [], q
            else:
                keyword = await _recall(owner, ws, c.titles)
    print(f"Recall@5 hybrid {hybrid:.2f}  keyword-only {keyword:.2f}")
    assert max(targets) < min(off)  # a floor exists that separates the two sets
    assert hybrid > keyword
