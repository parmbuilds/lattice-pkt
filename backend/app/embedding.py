"""The one seam code obtains embeddings through (KTD1)."""

import asyncio
import hashlib
import math
import re
import threading
from importlib.metadata import version
from typing import Protocol

from fastembed import TextEmbedding

from app.config import Settings

DIMENSION = 384


class EmbedderUnavailable(RuntimeError):
    """The model cannot be loaded, so search answers from keywords only (R19)."""


class Embedder(Protocol):
    dimension: int
    # Model name, library version and dimension; part of the derivation config hash (KTD10).
    config_identity: str

    async def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class FakeEmbedder:
    """Deterministic test embedder: words hash into buckets, so shared words mean similarity."""

    dimension = DIMENSION
    config_identity = f"fake-v1;{DIMENSION}"

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for word in re.findall(r"\w+", text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest) % self.dimension] += 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        if not norm:
            vector[0] = 1.0  # wordless text still gets a unit vector, never a zero one
            return vector
        return [x / norm for x in vector]

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FastEmbedder:
    """fastembed from the local cache only; the image warms it at build time (KTD1)."""

    model_name = "BAAI/bge-small-en-v1.5"

    def __init__(self, settings: Settings):
        self.dimension = TextEmbedding.get_embedding_size(self.model_name)
        self.config_identity = (
            f"{self.model_name};fastembed=={version('fastembed')};{self.dimension}"
        )
        self._cache_path = settings.embedding_cache_path
        self._threads = settings.embedder_threads
        self._batch_size = settings.embed_batch_size
        self._model: TextEmbedding | None = None
        # Held per batch, never per document, so a query waits for at most one batch.
        self._lock = threading.Lock()

    def _embed(self, kind: str, texts: list[str]) -> list[list[float]]:
        with self._lock:
            if self._model is None:
                try:
                    self._model = TextEmbedding(
                        self.model_name,
                        cache_dir=self._cache_path,
                        threads=self._threads,
                        local_files_only=True,
                    )
                except ValueError as error:
                    raise EmbedderUnavailable(
                        f"{self.model_name} is not in the local model cache; "
                        "warm it as the Dockerfile does (FASTEMBED_CACHE_PATH)."
                    ) from error
            embed = self._model.passage_embed if kind == "passage" else self._model.query_embed
            return [vector.tolist() for vector in embed(texts)]

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors += await asyncio.to_thread(self._embed, "passage", batch)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await asyncio.to_thread(self._embed, "query", [text]))[0]


def make_embedder(settings: Settings) -> Embedder:
    embedder = FakeEmbedder() if settings.embedder == "fake" else FastEmbedder(settings)
    if embedder.dimension != DIMENSION:
        raise RuntimeError(f"The embedder must produce {DIMENSION} dimensions.")
    return embedder
