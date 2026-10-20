"""The bill of derivatives (KTD9) and the lifecycle purge it drives (KTD8, R12, R13)."""

from uuid import UUID

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.models import Base, Conversation, Document

DELETE_BY_VERSION = "delete by version"
CASCADE = "cascades from derivations"
WHOLE_PARENT = "deletes its whole turn or summary"

# A stale span deletes the turn or summary it belongs to, whose rows then cascade (KTD4).
PARENTS = {
    "turn_passages": ("turns", "turn_id"),
    "summary_sources": ("conversation_summaries", "summary_id"),
}

# Every table derived from document versions, with its purge rule. test_schema fails on a table
# with a foreign key to document_versions that is missing here. Order matters: a cascaded table
# is counted before its parent's delete removes it, and deletes follow the lock order
# documents -> conversations -> turns, and documents -> derivations -> chunks -> intake_payloads.
DERIVED_TABLES = {
    "turn_passages": WHOLE_PARENT,
    "summary_sources": WHOLE_PARENT,
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
        if rule == WHOLE_PARENT:
            parent_name, key = PARENTS[name]
            # Counted only when present, so receipts of workspaces without Ask are unchanged.
            if count := await _delete_parents(db, name, parent_name, key, stale):
                removed[parent_name] = count
        elif rule == CASCADE:
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


async def _delete_parents(db: AsyncSession, name: str, parent_name: str, key: str, stale) -> int:
    """Delete every turn or summary with a stale row in `name`, after marking its conversation,
    so the asker's next look shows how much was left out (R15, R17)."""
    table, parent = Base.metadata.tables[name], Base.metadata.tables[parent_name]
    ids = select(table.c[key]).where(*stale).distinct().scalar_subquery()
    hits = (
        await db.execute(
            select(parent.c.conversation_id, func.count())
            .where(parent.c.id.in_(ids))
            .group_by(parent.c.conversation_id)
            .order_by(parent.c.conversation_id)  # one lock order across concurrent purges
        )
    ).all()
    for conversation_id, count in hits:
        mark = (
            {"left_out": Conversation.left_out + count}
            if parent_name == "turns"
            else {"summary_dropped": True}
        )
        await db.execute(
            update(Conversation).where(Conversation.id == conversation_id).values(**mark)
        )
    return (await db.execute(delete(parent).where(parent.c.id.in_(ids)))).rowcount
