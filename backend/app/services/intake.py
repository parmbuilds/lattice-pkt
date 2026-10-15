"""Uploads (KTD11, KTD13): the request gate, title rule, dedupe, and the intake records."""

import re
from dataclasses import asdict
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import cookie_parser

from app import security
from app.auth import cookie_name, reject
from app.config import Settings
from app.db import CommitThenRaise
from app.ingest import Extraction, Receipt
from app.models import Document, DocumentVersion, Intake, IntakePayload
from app.policy import Subject
from app.services.documents import MAX_TITLE

MAX_TEXT = 256  # intakes check filename and reason lengths
UPLOAD_PATH = re.compile(r"/api/workspaces/[^/]+/documents/upload")

RECEIPT_NOT_ACKNOWLEDGED = HTTPException(
    409, "This draft's extraction receipt has flags. Review it and acknowledge that receipt."
)


class UploadGate:
    """Runs before multipart parsing and any database work on the upload path (KTD11 step 1):
    a session cookie is required, at most `upload_max_inflight` uploads run at once, and the
    body is counted as it streams, so a missing or false Content-Length cannot pass the limit."""

    def __init__(self, app, settings: Settings):
        self.app = app
        self.cookie = cookie_name(settings)
        self.max_bytes = settings.upload_max_bytes
        self.max_inflight = settings.upload_max_inflight
        self.inflight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not UPLOAD_PATH.fullmatch(scope["path"]):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        session = cookie_parser(headers.get("cookie", "")).get(self.cookie, "")
        if not security.parse_prefix(session, "ses"):  # the session itself is checked later
            return await reject(send, 401, "Sign in required.")
        if self.inflight >= self.max_inflight:
            return await reject(send, 429, "Too many uploads in progress. Try again shortly.")
        if int(headers.get("content-length") or 0) > self.max_bytes:
            return await reject(send, 413, "This file is over the upload size limit.")
        received = 0

        async def counted():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.max_bytes:  # FastAPI re-raises an HTTPException from body parsing
                raise HTTPException(413, "This file is over the upload size limit.")
            return message

        self.inflight += 1
        try:
            await self.app(scope, counted, send)
        finally:
            self.inflight -= 1


def clean_title(title: str) -> str:
    """Invisible characters stripped, then cut to the title limit (KTD11 title rule)."""
    return Receipt().clean(title, "title").strip()[:MAX_TITLE].strip()


def filename_title(filename: str) -> str:
    return clean_title(PurePosixPath(filename).stem)


async def current_title(db: AsyncSession, doc: Document) -> str:
    """The pending title, else the serving one, else the latest version's."""
    current = doc.pending_version_id or doc.serving_version_id
    return await db.scalar(
        select(DocumentVersion.title)
        .where(DocumentVersion.document_id == doc.id)
        .order_by(desc(DocumentVersion.id == current), desc(DocumentVersion.created_at))
        .limit(1)
    )


def _own_live(subject: Subject):
    """The uploader's own live documents, joined later to their current versions (R5)."""
    return select(Document).where(
        Document.workspace_id == subject.workspace_id,
        Document.author_id == subject.user_id,
        Document.withdrawn_at.is_(None),
    )


_CURRENT = (Document.pending_version_id, Document.serving_version_id)


async def duplicate_of(db: AsyncSession, subject: Subject, source_sha256: str) -> Document | None:
    """The uploader's live document whose current version came from these exact bytes.

    The advisory lock holds until commit, so two identical uploads cannot both create.
    """
    key = f"{subject.workspace_id}:{subject.user_id}:{source_sha256}"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
    return await db.scalar(
        _own_live(subject)
        .join(Intake, Intake.document_id == Document.id)
        .where(Intake.source_sha256 == source_sha256, Intake.version_id.in_(_CURRENT))
        .limit(1)
    )


async def titled(db: AsyncSession, subject: Subject, title: str) -> Document | None:
    """The uploader's live document with this current title, ignoring case and edge spaces."""
    return await db.scalar(
        _own_live(subject)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            DocumentVersion.id.in_(_CURRENT),
            func.lower(func.trim(DocumentVersion.title)) == func.lower(title),
        )
        .order_by(Document.created_at)
        .limit(1)
    )


async def rejected(
    db: AsyncSession, subject: Subject, filename: str, source_sha256: str, reason: str, status: int
) -> CommitThenRaise:
    """Record a refusal after authorization with no receipt or bytes (R2, KTD11 step 6)."""
    db.add(
        Intake(
            workspace_id=subject.workspace_id,
            actor_id=subject.user_id,
            filename=filename[:MAX_TEXT],
            reason=reason[:MAX_TEXT],
            outcome="rejected",
            source_sha256=source_sha256,
        )
    )
    await db.flush()
    return CommitThenRaise(HTTPException(status, reason[:MAX_TEXT]))


async def accepted(
    db: AsyncSession,
    subject: Subject,
    version: DocumentVersion,
    filename: str,
    data: bytes,
    source_sha256: str,
    extraction: Extraction,
) -> None:
    """The permanent intake row, then its purgeable payload (KTD11 step 5, KTD13)."""
    receipt = extraction.receipt
    intake = Intake(
        workspace_id=subject.workspace_id,
        actor_id=subject.user_id,
        filename=filename[:MAX_TEXT],
        detected_type=extraction.detected_type,
        parser=extraction.parser,
        parser_version=extraction.parser_version,
        outcome="accepted",
        receipt=receipt.permanent(),
        receipt_sha256=receipt.sha256,
        source_sha256=source_sha256,
        document_id=version.document_id,
        version_id=version.id,
    )
    db.add(intake)
    await db.flush()
    db.add(
        IntakePayload(
            intake_id=intake.id,
            workspace_id=subject.workspace_id,
            document_id=version.document_id,
            version_id=version.id,
            original=data,
            hidden_excerpts=[asdict(hidden) for hidden in receipt.hidden],
        )
    )
    await db.flush()


async def intake_of(db: AsyncSession, version_id: UUID) -> Intake | None:
    return await db.scalar(select(Intake).where(Intake.version_id == version_id))


async def check_acknowledged(db: AsyncSession, version_id: UUID, acknowledged: str | None):
    """A flagged receipt must be acknowledged by its exact hash (R9); returns what to store."""
    intake = await intake_of(db, version_id)
    if intake is None:  # typed or JSON-edited versions have no receipt
        return None
    if acknowledged == intake.receipt_sha256:
        return acknowledged
    if intake.receipt["flags"]:
        raise RECEIPT_NOT_ACKNOWLEDGED
    return None
