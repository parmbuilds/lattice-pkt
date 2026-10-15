"""Audit trail (R28, R29, R34). Every decision is recorded in the request transaction (KTD8)."""

import logging
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import CommitThenRaise
from app.models import AuditEvent
from app.policy import Action, Decision, DocumentState, Role, Subject, decide

log = logging.getLogger("lattice.audit")

MAX_TEXT = 256  # stored queries and attempted-override values are truncated (Assumptions)


NOT_FOUND_DETAIL = "Not found or not permitted."


def not_found() -> HTTPException:
    """The one response for anything missing or not permitted (R29)."""
    return HTTPException(status_code=404, detail=NOT_FOUND_DETAIL)


def _bounded(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, dict):
        return {str(k)[:MAX_TEXT]: _bounded(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_bounded(v) for v in value]
    return value


async def record(
    db: AsyncSession,
    subject: Subject,
    decision: Decision,
    *,
    document_id: UUID | None = None,
    version_id: UUID | None = None,
    returned_version_ids: Iterable[UUID] | None = None,
    detail: dict[str, Any] | None = None,
    holder_of_secret: bool = False,
) -> None:
    """Write one audit row. Never pass secrets or document bodies in `detail`.

    `holder_of_secret` admits a non-member who presented a genuine workspace secret (an invite
    link), so a rejected use is still traceable without letting outsiders flood the trail.
    """
    if subject.role is None and not holder_of_secret:
        # Outsiders cannot write into a workspace's trail (Assumptions); keep a log line instead.
        log.info(
            "non-member request user=%s workspace=%s action=%s",
            subject.user_id,
            subject.workspace_id,
            decision.inputs.get("action"),
        )
        return
    db.add(
        AuditEvent(
            workspace_id=subject.workspace_id,
            actor_user_id=subject.user_id,
            channel=subject.channel,
            token_id=subject.token_id,
            action=decision.inputs.get("action", "unknown"),
            outcome="allow" if decision.allowed else "deny",
            rule_id=decision.rule,
            inputs=_bounded(decision.inputs),
            document_id=document_id,
            version_id=version_id,
            returned_version_ids=list(returned_version_ids) if returned_version_ids else None,
            detail=_bounded(detail) if detail else None,
        )
    )
    # Flush now so an audit failure surfaces inside the action, not after it (R34).
    await db.flush()


async def record_system(
    db: AsyncSession,
    workspace_id: UUID,
    action: str,
    detail: dict[str, Any],
    *,
    document_id: UUID | None = None,
) -> None:
    """Write one row the system made on its own, such as a purge receipt (KTD8): no actor,
    channel 'system', flushed now so a failure undoes the action, like `record`."""
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=None,
            channel="system",
            action=action,
            outcome="allow",
            document_id=document_id,
            detail=_bounded(detail),
        )
    )
    await db.flush()


async def authorize(
    db: AsyncSession,
    subject: Subject,
    action: Action,
    document: DocumentState | None = None,
    *,
    version_id: UUID | None = None,
    target_group_id: UUID | None = None,
    target_role: Role | None = None,
    detail: dict[str, Any] | None = None,
    defer_allowed: bool = False,
) -> Decision:
    """Decide, record, and on denial end the request with the generic response.

    A denial still commits its audit row (KTD8), so it is raised through CommitThenRaise.
    With `defer_allowed`, an allowed decision is recorded by the caller once it knows the
    outcome (a search records the versions it returned).
    """
    decision = decide(
        subject, action, document, target_group_id=target_group_id, target_role=target_role
    )
    if decision.allowed and defer_allowed:
        return decision
    await record(
        db,
        subject,
        decision,
        document_id=document.document_id if document else None,
        version_id=version_id,
        detail=detail,
    )
    if not decision.allowed:
        raise CommitThenRaise(not_found())
    return decision
