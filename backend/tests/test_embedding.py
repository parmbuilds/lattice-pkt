import asyncio
import math
import time

import numpy
import psycopg
import pytest

from app import embedding
from app.config import Settings
from app.embedding import EmbedderUnavailable, FakeEmbedder, FastEmbedder
from tests.conftest import plain_dsn


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_the_test_database_has_the_vector_extension(pg):
    with psycopg.connect(plain_dsn(pg["superuser"])) as conn:
        installed = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
    assert installed


async def test_the_fake_embedder_is_deterministic_and_rewards_shared_words():
    fake = FakeEmbedder()
    a, b, c = await fake.embed_passages(
        [
            "Rotate the zirconium keys every quarter.",
            "Where are the zirconium keys kept?",
            "Lunch is served at noon.",
        ]
    )
    for vector in (a, b, c):
        assert len(vector) == 384
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0)
    assert await fake.embed_query("Rotate the zirconium keys every quarter.") == a
    assert _cosine(a, b) > _cosine(a, c)


class _SlowModel:
    """Stands in for fastembed's TextEmbedding: every call takes 50 ms."""

    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def get_embedding_size(model_name: str) -> int:
        return 384

    def passage_embed(self, texts):
        time.sleep(0.05)
        return [numpy.zeros(384) for _ in texts]

    query_embed = passage_embed


async def test_a_query_waits_for_at_most_one_passage_batch(monkeypatch):
    monkeypatch.setattr(embedding, "TextEmbedding", _SlowModel)
    embedder = FastEmbedder(Settings(embed_batch_size=1))
    passages = asyncio.create_task(embedder.embed_passages(["passage"] * 40))  # about 2 s
    await asyncio.sleep(0.1)
    started = time.monotonic()
    assert len(await embedder.embed_query("query")) == 384
    assert time.monotonic() - started < 0.5
    assert not passages.done()
    assert len(await passages) == 40


@pytest.mark.benchmark_model
async def test_fastembed_embeds_offline_from_the_cached_model():
    """Needs the model in FASTEMBED_CACHE_PATH or LATTICE_EMBEDDING_CACHE_PATH."""
    embedder = FastEmbedder(Settings())
    [passage] = await embedder.embed_passages(["Restart the checkout service."])
    query = await embedder.embed_query("How do I restart checkout?")
    assert len(passage) == len(query) == 384
    assert _cosine(passage, query) > 0.5


@pytest.mark.benchmark_model
async def test_a_missing_model_cache_raises_instead_of_downloading(tmp_path):
    embedder = FastEmbedder(Settings(embedding_cache_path=str(tmp_path)))
    with pytest.raises(EmbedderUnavailable, match="not in the local model cache"):
        await embedder.embed_query("anything")
    assert not list(tmp_path.rglob("*.onnx"))
