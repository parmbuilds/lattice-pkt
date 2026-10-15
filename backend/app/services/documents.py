"""Document lifecycle (R15-R18, R31, R32). Each transition is one guarded UPDATE (KTD4)."""

import difflib
import unicodedata
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import not_found
from app.models import Document, DocumentGrant, DocumentVersion, VersionDecision
from app.policy import DocumentState, Subject

MAX_TITLE = 200
MAX_BODY = 200_000

STALE_DRAFT = HTTPException(409, "This draft was replaced or already decided. Reload and retry.")
DOCUMENT_CHANGED = HTTPException(409, "The document changed. Reload and retry.")


def validate(title: str, body: str) -> tuple[str, str]:
    title = title.strip()
    if not 1 <= len(title) <= MAX_TITLE or not 1 <= len(body) <= MAX_BODY:
        raise HTTPException(400, f"Titles need 1-{MAX_TITLE} characters, bodies 1-{MAX_BODY}.")
    return title, body


async def load(
    db: AsyncSession, subject: Subject, document_id: UUID
) -> tuple[Document, DocumentState]:
    """The document and the facts the policy needs. Missing looks like not permitted (R29)."""
    doc = await db.scalar(
        select(Document).where(
            Document.id == document_id, Document.workspace_id == subject.workspace_id
        )
    )
    if doc is None:
        raise not_found()
    grants = (
        await db.execute(
            select(DocumentGrant.group_id, DocumentGrant.user_id).where(
                DocumentGrant.document_id == doc.id
            )
        )
    ).all()
    state = DocumentState(
        workspace_id=doc.workspace_id,
        document_id=doc.id,
        author_id=doc.author_id,
        has_serving=doc.serving_version_id is not None,
        has_pending=doc.pending_version_id is not None,
        withdrawn=doc.withdrawn_at is not None,
        shared_group_ids=frozenset(g for g, _ in grants if g),
        shared_user_ids=frozenset(u for _, u in grants if u),
    )
    return doc, state


def for_version(doc: Document, state: DocumentState, version_id: UUID | None) -> DocumentState:
    return replace(
        state,
        version_is_serving=version_id is not None and version_id == doc.serving_version_id,
    )


async def version_of(db: AsyncSession, doc: Document, version_id: UUID) -> DocumentVersion:
    version = await db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == version_id, DocumentVersion.document_id == doc.id
        )
    )
    if version is None:
        raise not_found()
    return version


async def new_draft(
    db: AsyncSession, subject: Subject, doc: Document, title: str, body: str
) -> DocumentVersion:
    """Versions never change; an edit adds a version that replaces any pending draft (R17)."""
    version = DocumentVersion(
        workspace_id=doc.workspace_id,
        document_id=doc.id,
        title=title,
        body=body,
        created_by=subject.user_id,
    )
    db.add(version)
    await db.flush()
    result = await db.execute(
        update(Document)
        .where(Document.id == doc.id, Document.withdrawn_at.is_(None))
        .values(pending_version_id=version.id)
    )
    if result.rowcount == 0:
        raise DOCUMENT_CHANGED
    return version


async def decide_draft(
    db: AsyncSession,
    subject: Subject,
    doc: Document,
    version_id: UUID,
    decision: str,
    reason: str | None = None,
) -> None:
    """Approve, reject or discard the named draft, only if it is still the pending one (R16)."""
    values: dict = {"pending_version_id": None}
    if decision == "approved":
        values["serving_version_id"] = version_id
    result = await db.execute(
        update(Document)
        .where(
            Document.id == doc.id,
            Document.pending_version_id == version_id,
            Document.withdrawn_at.is_(None),
        )
        .values(**values)
    )
    if result.rowcount == 0:
        raise STALE_DRAFT
    db.add(
        VersionDecision(
            workspace_id=doc.workspace_id,
            document_id=doc.id,
            version_id=version_id,
            decision=decision,
            actor_id=subject.user_id,
            reason=(reason or "").strip()[:500] or None,
        )
    )
    await db.flush()


async def withdraw(db: AsyncSession, doc: Document) -> None:
    """Final in v1: stops every version being served and ends any pending draft (R18)."""
    result = await db.execute(
        update(Document)
        .where(Document.id == doc.id, Document.withdrawn_at.is_(None))
        .values(withdrawn_at=datetime.now(UTC), pending_version_id=None)
    )
    if result.rowcount == 0:
        raise DOCUMENT_CHANGED


def escape_invisible(text: str) -> str:
    """Show format characters (Unicode Cf, e.g. zero-width spaces) as visible escapes (KTD11)."""
    return "".join(f"\\u{ord(c):04x}" if unicodedata.category(c) == "Cf" else c for c in text)


def review_diff(serving: str | None, draft: str) -> list[str]:
    before = escape_invisible(serving or "").splitlines()
    after = escape_invisible(draft).splitlines()
    return list(difflib.unified_diff(before, after, "serving", "draft", lineterm="", n=3))
