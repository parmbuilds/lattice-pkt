"""The bill of derivatives (KTD9) and the lifecycle purge it drives (KTD8, R12, R13)."""

from uuid import UUID

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.models import Base, Document

DELETE_BY_VERSION = "delete by version"
CASCADE = "cascades from derivations"

# Every table derived from document versions, with its purge rule. test_schema fails on a table
# with a foreign key to document_versions that is missing here. Order matters: a cascaded table
# is counted before its parent's delete removes it, and deletes follow the lock order
# documents -> derivations -> chunks -> intake_payloads.
DERIVED_TABLES = {
    "chunks": CASCADE,
    "derivations": DELETE_BY_VERSION,
    "intake_payloads": DELETE_BY_VERSION,
}

RETAINED = "Version bodies are retained: document versions are immutable."


async def purge_document(db: AsyncSession, document_id: UUID, reason: str) -> None:
    """Delete derived rows of every version that is no longer pending or serving (all of them
    once withdrawn) and receipt the counts. Runs after the guarded lifecycle UPDATE, whose row
    lock keeps derivation writers out until commit, so the counts are exact."""
    removed = {}
    for name, rule in DERIVED_TABLES.items():
        table = Base.metadata.tables[name]
        current = exists().where(
            Document.id == table.c.document_id,
            Document.withdrawn_at.is_(None),
            or_(
                Document.pending_version_id == table.c.version_id,
                Document.serving_version_id == table.c.version_id,
            ),
        )
        stale = (table.c.document_id == document_id, ~current)
        if rule == CASCADE:
            removed[name] = await db.scalar(select(func.count()).select_from(table).where(*stale))
        else:
            removed[name] = (await db.execute(delete(table).where(*stale))).rowcount
    if not any(removed.values()):
        return
    workspace_id = await db.scalar(select(Document.workspace_id).where(Document.id == document_id))
    await audit.record_system(
        db,
        workspace_id,
        "purge",
        {"reason": reason, "removed": removed, "note": RETAINED},
        document_id=document_id,
    )
