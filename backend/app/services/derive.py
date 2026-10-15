"""Chunk and embed pending and serving versions, one core for inline and worker use (KTD3).

Lock order everywhere: documents -> derivations -> chunks -> intake_payloads.
"""

import asyncio
import hashlib
import logging
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from itertools import pairwise
from uuid import UUID

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.embedding import Embedder
from app.models import Chunk, Derivation, Document, DocumentVersion

log = logging.getLogger("lattice.derive")

CHUNKER_VERSION = "paragraphs-v1"
MAX_BACKOFF_SECONDS = 3600

Transaction = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def config_hash(embedder: Embedder) -> str:
    """The pipeline configuration chunks and approvals are keyed to (KTD10)."""
    identity = f"{embedder.config_identity};chunker={CHUNKER_VERSION}"
    return hashlib.sha256(identity.encode()).hexdigest()


def chunk_spans(body: str, max_chars: int) -> list[tuple[int, int]]:
    """Paragraph-aligned spans that tile the body, each at most max_chars long (KTD5)."""
    bounds = [m.end() for m in re.finditer(r"\n\s*\n", body)] + [len(body)]
    spans: list[tuple[int, int]] = []
    start = end = 0
    for bound in bounds:
        if bound - start > max_chars and end > start:
            spans.append((start, end))
            start = end
        while bound - start > max_chars:  # one paragraph over the cap: cut after a space
            limit = start + max_chars
            space = max(body.rfind(" ", start + 1, limit), body.rfind("\n", start + 1, limit))
            cut = space + 1 if space > start else limit
            spans.append((start, cut))
            start = cut
        end = bound
    if end > start:
        spans.append((start, end))
    assert spans[0][0] == 0 and spans[-1][1] == len(body), "spans must cover the body"
    assert all(a[1] == b[0] for a, b in pairwise(spans)), "spans must not gap or overlap"
    return spans


async def _embed(embedder: Embedder, title: str, body: str, max_chars: int) -> list[dict]:
    spans = chunk_spans(body, max_chars)
    # The title prefix is recomputed here and never stored (KTD5).
    vectors = await embedder.embed_passages([f"{title}\n\n{body[s:e]}" for s, e in spans])
    assert len(vectors) == len(spans)
    return [
        {
            "ordinal": i,
            "start": s,
            "end": e,
            "sha256": hashlib.sha256(body[s:e].encode()).hexdigest(),
            "embedding": vector,
        }
        for i, ((s, e), vector) in enumerate(zip(spans, vectors, strict=True))
    ]


async def derive(
    transaction: Transaction, embedder: Embedder, settings: Settings, version_id: UUID
) -> None:
    """Read, embed with no locks held, then write only if the version is still current."""
    current = config_hash(embedder)
    ledger = (Derivation.version_id == version_id, Derivation.config_hash == current)
    async with transaction() as db:
        source = (
            await db.execute(
                select(
                    DocumentVersion.workspace_id,
                    DocumentVersion.document_id,
                    DocumentVersion.title,
                    DocumentVersion.body,
                    Derivation.attempts,
                )
                .join(Derivation, Derivation.version_id == DocumentVersion.id)
                .where(*ledger, Derivation.status != "ready")
            )
        ).first()
    if source is None:
        return
    try:
        chunks = await _embed(embedder, source.title, source.body, settings.chunk_max_chars)
    except Exception as error:
        log.warning("derivation of version %s failed", version_id, exc_info=True)
        backoff = min(settings.derive_poll_seconds * 2**source.attempts, MAX_BACKOFF_SECONDS)
        async with transaction() as db:
            await db.execute(
                update(Derivation)
                .where(*ledger, Derivation.status != "ready")
                .values(
                    status="failed",
                    error=f"{type(error).__name__}: {error}"[:500],
                    attempts=Derivation.attempts + 1,
                    next_attempt_at=func.now() + timedelta(seconds=backoff),
                    updated_at=func.now(),
                )
            )
        return

    async with transaction() as db:
        doc = (
            await db.execute(
                select(
                    Document.pending_version_id, Document.serving_version_id, Document.withdrawn_at
                )
                .where(Document.id == source.document_id)
                .with_for_update(read=True)
            )
        ).one()
        status = await db.scalar(select(Derivation.status).where(*ledger).with_for_update())
        if status is None or status == "ready":
            return
        if (
            doc.withdrawn_at is not None
            or version_id not in (doc.pending_version_id, doc.serving_version_id)
            or config_hash(embedder) != current
        ):
            await db.execute(delete(Derivation).where(*ledger))
            return
        await db.execute(
            insert(Chunk),
            [
                {
                    "workspace_id": source.workspace_id,
                    "document_id": source.document_id,
                    "version_id": version_id,
                    "config_hash": current,
                    **chunk,
                }
                for chunk in chunks
            ],
        )
        await db.execute(
            update(Derivation)
            .where(*ledger)
            .values(status="ready", error=None, updated_at=func.now())
        )
        await db.execute(
            delete(Derivation).where(
                Derivation.version_id == version_id, Derivation.config_hash != current
            )
        )


# Takes each document row FOR SHARE so the enqueue serializes with lifecycle purges (KTD3).
SWEEP = text("""
INSERT INTO derivations (workspace_id, document_id, version_id, config_hash, status)
SELECT d.workspace_id, d.id, v.version_id, :hash, 'pending'
FROM documents d
CROSS JOIN LATERAL (VALUES (d.pending_version_id), (d.serving_version_id)) AS v (version_id)
WHERE d.withdrawn_at IS NULL
  AND v.version_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM derivations x WHERE x.version_id = v.version_id AND x.config_hash = :hash
  )
ORDER BY d.id
FOR SHARE OF d
ON CONFLICT (version_id, config_hash) DO NOTHING
""")


async def sweep(sessionmaker: async_sessionmaker, embedder: Embedder) -> None:
    """Enqueue current versions of live documents with no row at the current config."""
    async with sessionmaker.begin() as db:
        await db.execute(SWEEP, {"hash": config_hash(embedder)})


async def claim(
    sessionmaker: async_sessionmaker, embedder: Embedder, settings: Settings
) -> UUID | None:
    """Lease one due row, drafts first, and commit before deriving it."""
    async with sessionmaker.begin() as db:
        version_id = await db.scalar(
            select(Derivation.version_id)
            .join(Document, Document.id == Derivation.document_id)
            .where(
                Derivation.config_hash == config_hash(embedder),
                Derivation.status != "ready",
                Derivation.next_attempt_at <= func.now(),
            )
            .order_by(
                Document.pending_version_id.is_distinct_from(Derivation.version_id),
                Derivation.next_attempt_at,
            )
            .limit(1)
            .with_for_update(skip_locked=True, of=Derivation)
        )
        if version_id is not None:
            await db.execute(
                update(Derivation)
                .where(
                    Derivation.version_id == version_id,
                    Derivation.config_hash == config_hash(embedder),
                )
                .values(
                    next_attempt_at=func.now() + timedelta(seconds=settings.derive_lease_seconds)
                )
            )
    return version_id


async def tick(
    sessionmaker: async_sessionmaker, embedder: Embedder, settings: Settings
) -> UUID | None:
    """One worker step: sweep, claim and derive. Returns the version it worked on, if any."""
    await sweep(sessionmaker, embedder)
    version_id = await claim(sessionmaker, embedder, settings)
    if version_id is not None:
        await derive(sessionmaker.begin, embedder, settings, version_id)
    return version_id


async def run_worker(
    sessionmaker: async_sessionmaker, embedder: Embedder, settings: Settings
) -> None:
    """The in-process worker loop the app lifespan runs (KTD3)."""
    while True:
        try:
            if await tick(sessionmaker, embedder, settings) is not None:
                continue  # drain due work before sleeping
        except Exception:
            log.exception("derivation worker tick failed")
        await asyncio.sleep(settings.derive_poll_seconds)
