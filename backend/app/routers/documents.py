import hashlib
from dataclasses import replace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import and_, delete, desc, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.audit import authorize, not_found
from app.auth import Member
from app.db import DB
from app.ingest import Limits, Rejected, runner
from app.models import (
    Derivation,
    Document,
    DocumentGrant,
    DocumentVersion,
    Group,
    IntakePayload,
    User,
    VersionDecision,
)
from app.policy import Action, decide, read_clause
from app.services import documents as svc
from app.services import intake
from app.services.derive import config_hash
from app.services.workspaces import membership

router = APIRouter(prefix="/api/workspaces/{workspace_id}/documents", tags=["documents"])


class DocumentBody(BaseModel):
    title: str
    body: str


class VersionRef(BaseModel):
    version_id: UUID
    reason: str | None = None
    acknowledged_receipt: str | None = None  # the receipt sha256 the approver reviewed (R9)


class NewAuthor(BaseModel):
    user_id: UUID


def _summary(doc: Document, *, serving: UUID | None, pending: UUID | None) -> dict:
    return {
        "id": str(doc.id),
        "author_id": str(doc.author_id),
        "withdrawn": doc.withdrawn_at is not None,
        "serving_version_id": str(serving) if serving else None,
        "pending_version_id": str(pending) if pending else None,
    }


@router.post("", status_code=201)
async def create_document(body: DocumentBody, request: Request, subject: Member, db: DB) -> dict:
    title, text = svc.validate(body.title, body.body)
    await authorize(db, subject, Action.CREATE_DOCUMENT)
    doc = Document(workspace_id=subject.workspace_id, author_id=subject.user_id)
    db.add(doc)
    await db.flush()
    app = request.app.state
    # Starts unshared (R11).
    version = await svc.new_draft(db, subject, doc, title, text, app.embedder, app.settings)
    return _summary(doc, serving=None, pending=version.id)


@router.post("/upload", status_code=201)
async def upload(
    request: Request,
    response: Response,
    subject: Member,
    db: DB,
    file: UploadFile,
    title: Annotated[str | None, Form()] = None,
    document_id: Annotated[UUID | None, Form()] = None,
    new_document: Annotated[bool, Form()] = False,
) -> dict:
    """A file becomes a draft through the typed path (R1-R6). `intake.UploadGate` has already
    checked the session cookie, the in-flight cap and the byte limit (KTD11)."""
    app = request.app.state
    data, filename = await file.read(), file.filename or ""
    digest = hashlib.sha256(data).hexdigest()
    doc = None
    if document_id:  # a new version follows the edit rules
        doc, state = await svc.load(db, subject, document_id)
        await authorize(db, subject, Action.EDIT, state)
    else:
        await authorize(db, subject, Action.CREATE_DOCUMENT)
    try:
        extraction = await runner.extract_in_child(
            data, filename, Limits.from_settings(app.settings)
        )
    except Rejected as error:
        raise await intake.rejected(
            db, subject, filename, digest, error.reason, error.status
        ) from None
    body = extraction.body
    if not 1 <= len(body) <= svc.MAX_BODY:
        reason = "body_too_long" if body else "no_text"
        raise await intake.rejected(db, subject, filename, digest, reason, 422)
    if doc is None:
        title = intake.clean_title(title) if title is not None else intake.filename_title(filename)
        if existing := await intake.duplicate_of(db, subject, digest):
            response.status_code = 200
            pending, serving = existing.pending_version_id, existing.serving_version_id
            return _summary(existing, serving=serving, pending=pending) | {"created": False}
        if not new_document and (existing := await intake.titled(db, subject, title)):
            return JSONResponse(  # offered as a new version of it (R5)
                {
                    "detail": "You have a document with this title.",
                    "suggest_document_id": str(existing.id),
                },
                status_code=409,
            )
    else:
        title = intake.clean_title(
            title if title is not None else await intake.current_title(db, doc)
        )
    title, body = svc.validate(title, body)
    try:
        # A savepoint, so a body too diverse to index can still be recorded as a rejection.
        async with db.begin_nested():
            if doc is None:
                doc = Document(workspace_id=subject.workspace_id, author_id=subject.user_id)
                db.add(doc)
                await db.flush()
            version = await svc.new_draft(db, subject, doc, title, body, app.embedder, app.settings)
    except HTTPException as error:
        if error is not svc.BODY_TOO_DIVERSE:
            raise
        raise await intake.rejected(
            db, subject, filename, digest, "body_too_diverse", 422
        ) from None
    await intake.accepted(db, subject, version, filename, data, digest, extraction)
    return _summary(doc, serving=doc.serving_version_id, pending=version.id) | {"created": True}


@router.get("")
async def list_documents(subject: Member, db: DB) -> dict:
    """Each document once, showing only the versions this person may view (R20)."""
    await authorize(db, subject, Action.LIST_DOCUMENTS)
    d = Document.__table__
    sv = DocumentVersion.__table__.alias("sv")
    pv = DocumentVersion.__table__.alias("pv")
    lv = DocumentVersion.__table__.alias("lv")
    # A rejected or discarded first draft has neither; show its latest version, as detail does.
    latest = (
        select(lv.c.id)
        .where(lv.c.document_id == d.c.id)
        .order_by(desc(lv.c.created_at))
        .limit(1)
        .scalar_subquery()
    )
    fallback = DocumentVersion.__table__.alias("fv")
    serving_visible = read_clause(subject, Action.VIEW, d, sv)
    pending_visible = read_clause(subject, Action.VIEW, d, pv)
    fallback_visible = read_clause(subject, Action.VIEW, d, fallback)
    rows = await db.execute(
        select(
            d.c.id,
            d.c.author_id,
            d.c.withdrawn_at,
            sv.c.id,
            sv.c.title,
            pv.c.id,
            pv.c.title,
            fallback.c.title,
            serving_visible,
            pending_visible,
            fallback_visible,
            User.display_name,
        )
        .select_from(
            d.outerjoin(sv, sv.c.id == d.c.serving_version_id)
            .outerjoin(pv, pv.c.id == d.c.pending_version_id)
            .outerjoin(
                fallback,
                and_(
                    d.c.serving_version_id.is_(None),
                    d.c.pending_version_id.is_(None),
                    fallback.c.id == latest,
                ),
            )
            .join(User, User.id == d.c.author_id)
        )
        .where(
            d.c.workspace_id == subject.workspace_id,
            or_(serving_visible, pending_visible, fallback_visible),
        )
        .order_by(desc(d.c.created_at))
    )
    documents = []
    for row in rows:
        doc_id, author_id, withdrawn_at, s_id, s_title, p_id, p_title, f_title = row[:8]
        s_ok, p_ok, f_ok, author = row[8:]
        documents.append(
            {
                "id": str(doc_id),
                "title": (p_title if p_ok else None)
                or (s_title if s_ok else None)
                or (f_title if f_ok else None),
                "author_id": str(author_id),
                "author_name": author,
                "withdrawn": withdrawn_at is not None,
                "serving_version_id": str(s_id) if s_ok and s_id else None,
                "pending_version_id": str(p_id) if p_ok and p_id else None,
            }
        )
    return {"documents": documents}


@router.get("/{document_id}")
async def get_document(document_id: UUID, request: Request, subject: Member, db: DB) -> dict:
    doc, state = await svc.load(db, subject, document_id)
    # A discarded first draft leaves no serving or pending version; fall back to history.
    primary = (
        doc.serving_version_id
        or doc.pending_version_id
        or await db.scalar(
            select(DocumentVersion.id)
            .where(DocumentVersion.document_id == doc.id)
            .order_by(desc(DocumentVersion.created_at))
            .limit(1)
        )
    )
    await authorize(
        db, subject, Action.VIEW, svc.for_version(doc, state, primary), version_id=primary
    )
    sees_pending = decide(
        subject, Action.VIEW, svc.for_version(doc, state, doc.pending_version_id)
    ).allowed
    pending = doc.pending_version_id if sees_pending else None
    shown = await db.get(DocumentVersion, pending or primary)
    result = _summary(doc, serving=doc.serving_version_id, pending=pending) | {
        # Same name the document list already shows to anyone who can see this document.
        "author_name": await db.scalar(select(User.display_name).where(User.id == doc.author_id)),
        "title": shown.title,
        "body": shown.body,
        "shown_version_id": str(shown.id),
    }
    if sees_pending:  # author or manager: history, sharing and the last decision
        result["versions"] = [
            {"id": str(vid), "title": title, "created_at": created_at.isoformat()}
            for vid, title, created_at in await db.execute(
                select(DocumentVersion.id, DocumentVersion.title, DocumentVersion.created_at)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.created_at)
            )
        ]
        # Includes groups the author has left, so they can still unshare (R12, Assumptions).
        result["shared_with"] = [
            {"id": str(gid), "name": name}
            for gid, name in await db.execute(
                select(Group.id, Group.name)
                .join(DocumentGrant, DocumentGrant.group_id == Group.id)
                .where(DocumentGrant.document_id == doc.id)
                .order_by(Group.name)
            )
        ]
        last = await db.scalar(
            select(VersionDecision)
            .where(VersionDecision.document_id == doc.id)
            .order_by(desc(VersionDecision.created_at))
            .limit(1)
        )
        result["last_decision"] = (
            {"decision": last.decision, "reason": last.reason, "version_id": str(last.version_id)}
            if last
            else None
        )
        # Derivation status of the current versions; no row yet means the worker will enqueue it.
        current = [v for v in (doc.pending_version_id, doc.serving_version_id) if v]
        rows = {
            vid: {"status": status, "error": error}
            for vid, status, error in await db.execute(
                select(Derivation.version_id, Derivation.status, Derivation.error).where(
                    Derivation.version_id.in_(current),
                    Derivation.config_hash == config_hash(request.app.state.embedder),
                )
            )
        }
        result["derivations"] = {
            str(vid): rows.get(vid, {"status": "pending", "error": None}) for vid in current
        }
    return result


@router.get("/{document_id}/versions/{version_id}")
async def get_version(document_id: UUID, version_id: UUID, subject: Member, db: DB) -> dict:
    doc, state = await svc.load(db, subject, document_id)
    version = await svc.version_of(db, doc, version_id)
    await authorize(
        db, subject, Action.VIEW, svc.for_version(doc, state, version.id), version_id=version.id
    )
    return {"id": str(version.id), "title": version.title, "body": version.body}


@router.put("/{document_id}")
async def edit_document(
    document_id: UUID, body: DocumentBody, request: Request, subject: Member, db: DB
) -> dict:
    title, text = svc.validate(body.title, body.body)
    doc, state = await svc.load(db, subject, document_id)
    await authorize(db, subject, Action.EDIT, state)
    app = request.app.state
    version = await svc.new_draft(db, subject, doc, title, text, app.embedder, app.settings)
    return _summary(doc, serving=doc.serving_version_id, pending=version.id)


@router.get("/{document_id}/review")
async def review(document_id: UUID, request: Request, subject: Member, db: DB) -> dict:
    """The draft against the serving version, as raw text (R35, KTD11), with the upload's
    receipt and hidden excerpts (R8) and the draft's derivation status (R11)."""
    doc, state = await svc.load(db, subject, document_id)
    await authorize(db, subject, Action.REVIEW, state, version_id=doc.pending_version_id)
    draft = await db.get(DocumentVersion, doc.pending_version_id)
    serving = (
        await db.get(DocumentVersion, doc.serving_version_id) if doc.serving_version_id else None
    )
    source = await intake.intake_of(db, draft.id)
    payload = await db.get(IntakePayload, source.id) if source else None
    derivation = (
        await db.execute(
            select(Derivation.status, Derivation.error).where(
                Derivation.version_id == draft.id,
                Derivation.config_hash == config_hash(request.app.state.embedder),
            )
        )
    ).first()
    return {
        "document_id": str(doc.id),
        "pending_version_id": str(draft.id),
        "serving_version_id": str(serving.id) if serving else None,
        "author_id": str(doc.author_id),
        "title": svc.escape_invisible(draft.title),
        "diff": svc.review_diff(serving.body if serving else None, draft.body),
        "receipt": source.receipt
        | {"sha256": source.receipt_sha256, "hidden_excerpts": payload.hidden_excerpts}
        if source
        else None,
        "intake": {
            "id": str(source.id),
            "filename": svc.escape_invisible(source.filename),
            "detected_type": source.detected_type,
            "parser": source.parser,
            "parser_version": source.parser_version,
            "source_sha256": source.source_sha256,
        }
        if source
        else None,
        # No row yet means the worker will enqueue it.
        "derivation": {"status": derivation.status, "error": derivation.error}
        if derivation
        else {"status": "pending", "error": None},
    }


async def _decide_draft(
    document_id, body: VersionRef, request: Request, subject, db, action, decision
) -> DocumentVersion:
    doc, state = await svc.load(db, subject, document_id)
    version = await svc.version_of(db, doc, body.version_id)
    # The named draft is the target; whether it is still pending is checked atomically below.
    target = svc.for_version(doc, state, version.id)
    await authorize(
        db,
        subject,
        action,
        replace(target, has_pending=True),
        version_id=version.id,
        detail={"self_approved": version.created_by == subject.user_id}
        if action is Action.APPROVE
        else None,
    )
    acknowledged = (
        await intake.check_acknowledged(db, version.id, body.acknowledged_receipt)
        if action is Action.APPROVE
        else None
    )
    await svc.decide_draft(
        db,
        subject,
        doc,
        version.id,
        decision,
        config_hash(request.app.state.embedder),
        body.reason,
        acknowledged,
    )
    return version


@router.post("/{document_id}/approve")
async def approve(
    document_id: UUID, body: VersionRef, request: Request, subject: Member, db: DB
) -> dict:
    version = await _decide_draft(
        document_id, body, request, subject, db, Action.APPROVE, "approved"
    )
    return {"serving_version_id": str(version.id)}


@router.post("/{document_id}/reject", status_code=204)
async def reject(
    document_id: UUID, body: VersionRef, request: Request, subject: Member, db: DB
) -> None:
    await _decide_draft(document_id, body, request, subject, db, Action.REJECT, "rejected")


@router.post("/{document_id}/discard", status_code=204)
async def discard(
    document_id: UUID, body: VersionRef, request: Request, subject: Member, db: DB
) -> None:
    await _decide_draft(document_id, body, request, subject, db, Action.DISCARD, "discarded")


@router.post("/{document_id}/withdraw", status_code=204)
async def withdraw(document_id: UUID, subject: Member, db: DB) -> None:
    doc, state = await svc.load(db, subject, document_id)
    await authorize(db, subject, Action.WITHDRAW, state)
    await svc.withdraw(db, doc)


@router.put("/{document_id}/shares/{group_id}", status_code=204)
async def share(document_id: UUID, group_id: UUID, subject: Member, db: DB) -> None:
    doc, state = await svc.load(db, subject, document_id)
    await authorize(
        db,
        subject,
        Action.SHARE,
        state,
        target_group_id=group_id,
        detail={"group_id": str(group_id)},
    )
    try:
        async with db.begin_nested():
            await db.execute(
                insert(DocumentGrant)
                .values(workspace_id=doc.workspace_id, document_id=doc.id, group_id=group_id)
                .on_conflict_do_nothing()
            )
    except IntegrityError:  # no such group in this workspace
        raise not_found() from None


@router.delete("/{document_id}/shares/{group_id}", status_code=204)
async def unshare(document_id: UUID, group_id: UUID, subject: Member, db: DB) -> None:
    doc, state = await svc.load(db, subject, document_id)
    await authorize(
        db,
        subject,
        Action.UNSHARE,
        state,
        target_group_id=group_id,
        detail={"group_id": str(group_id)},
    )
    await db.execute(
        delete(DocumentGrant).where(
            DocumentGrant.document_id == doc.id, DocumentGrant.group_id == group_id
        )
    )


@router.post("/{document_id}/author", status_code=204)
async def reassign_author(document_id: UUID, body: NewAuthor, subject: Member, db: DB) -> None:
    doc, state = await svc.load(db, subject, document_id)
    await authorize(
        db, subject, Action.REASSIGN_AUTHOR, state, detail={"new_author_id": str(body.user_id)}
    )
    if await membership(db, subject.workspace_id, body.user_id) is None:
        raise not_found()
    await db.execute(update(Document).where(Document.id == doc.id).values(author_id=body.user_id))
