"""Every authorization rule in Lattice lives in this module (KTD2).

Services call `decide` for single actions and `read_clause` / `readable_versions` to filter
queries. Read rules are written once as named terms; each term evaluates in Python for a
single decision and compiles to SQL for the in-query filter, so the two forms share one
definition (R26). No other module compares roles or lifecycle states.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, exists, false, or_, select, true

from app.models import Document, DocumentGrant, DocumentVersion

Role = Literal["owner", "admin", "member"]
Channel = Literal["ui", "mcp"]
MANAGERS = frozenset({"owner", "admin"})


@dataclass(frozen=True)
class Subject:
    """Who is acting, loaded live from the database for each request (R25)."""

    user_id: UUID
    workspace_id: UUID
    role: Role | None  # None: not a member of this workspace
    group_ids: frozenset[UUID] = frozenset()
    channel: Channel = "ui"
    token_id: UUID | None = None


@dataclass(frozen=True)
class DocumentState:
    """The facts about a document (and optionally one of its versions) a decision needs."""

    workspace_id: UUID
    document_id: UUID
    author_id: UUID
    has_serving: bool = False
    has_pending: bool = False
    withdrawn: bool = False
    shared_group_ids: frozenset[UUID] = frozenset()
    shared_user_ids: frozenset[UUID] = frozenset()
    version_is_serving: bool = False
    version_is_pending: bool = False


class Action(StrEnum):
    VIEW = "view"
    RETRIEVE = "retrieve"
    CREATE_DOCUMENT = "create_document"
    EDIT = "edit"
    SHARE = "share"
    UNSHARE = "unshare"
    APPROVE = "approve"
    REJECT = "reject"
    DISCARD = "discard"
    WITHDRAW = "withdraw"
    REASSIGN_AUTHOR = "reassign_author"
    MANAGE_GROUPS = "manage_groups"
    VIEW_ROSTER = "view_roster"
    VIEW_AUDIT = "view_audit"
    INVITE = "invite"
    REMOVE_MEMBER = "remove_member"
    CHANGE_ROLE = "change_role"
    TRANSFER_OWNERSHIP = "transfer_ownership"
    MANAGE_OWN_TOKENS = "manage_own_tokens"
    CREATE_WORKSPACE = "create_workspace"
    REVOKE_INVITE = "revoke_invite"
    ACCEPT_INVITE = "accept_invite"
    REVIEW = "review"
    LIST_DOCUMENTS = "list_documents"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule: str
    inputs: dict[str, Any] = field(default_factory=dict)


# --- Read rules: one definition, two forms -------------------------------------------------


@dataclass(frozen=True)
class _Tables:
    d: Any  # documents table or alias
    v: Any  # document_versions table or alias


@dataclass(frozen=True)
class _Term:
    holds: Callable[[Subject, DocumentState], bool]
    clause: Callable[[Subject, _Tables], ColumnElement[bool]]


def _shared_clause(s: Subject, t: _Tables) -> ColumnElement[bool]:
    g = DocumentGrant.__table__
    return exists().where(
        g.c.document_id == t.d.c.id,
        or_(g.c.group_id.in_(list(s.group_ids)), g.c.user_id == s.user_id),
    )


IS_MANAGER = _Term(
    holds=lambda s, r: s.role in MANAGERS,
    clause=lambda s, t: true() if s.role in MANAGERS else false(),
)
IS_AUTHOR = _Term(
    holds=lambda s, r: r.author_id == s.user_id,
    clause=lambda s, t: t.d.c.author_id == s.user_id,
)
SHARED = _Term(
    holds=lambda s, r: bool(r.shared_group_ids & s.group_ids) or s.user_id in r.shared_user_ids,
    clause=_shared_clause,
)
IS_SERVING = _Term(
    holds=lambda s, r: r.version_is_serving,
    clause=lambda s, t: t.d.c.serving_version_id == t.v.c.id,
)
NOT_WITHDRAWN = _Term(
    holds=lambda s, r: not r.withdrawn,
    clause=lambda s, t: t.d.c.withdrawn_at.is_(None),
)

# Each rule is a conjunction of terms; a read is allowed by the first rule that holds.
READ_RULES: dict[Action, list[tuple[str, tuple[_Term, ...]]]] = {
    # R19: retrieval returns only the serving version of a non-withdrawn document.
    Action.RETRIEVE: [
        ("retrieve.manager", (IS_SERVING, NOT_WITHDRAWN, IS_MANAGER)),  # R9
        ("retrieve.author", (IS_SERVING, NOT_WITHDRAWN, IS_AUTHOR)),  # R13
        ("retrieve.shared", (IS_SERVING, NOT_WITHDRAWN, SHARED)),  # R13
    ],
    # R20: the UI shows drafts and history to authors and managers only.
    Action.VIEW: [
        ("view.manager", (IS_MANAGER,)),  # R9
        ("view.author", (IS_AUTHOR,)),  # R11, R20
        ("view.shared", (IS_SERVING, NOT_WITHDRAWN, SHARED)),  # R13
    ],
}


def read_clause(subject: Subject, action: Action, d=None, v=None) -> ColumnElement[bool]:
    """SQL filter for the versions `subject` may VIEW or RETRIEVE (joined documents d, versions v).

    Goes in the WHERE clause, before ranking and LIMIT (R21).
    """
    t = _Tables(d=d if d is not None else Document.__table__, v=v if v is not None else _versions())
    if subject.role is None:
        return false()
    rules = [and_(*(term.clause(subject, t) for term in terms)) for _, terms in READ_RULES[action]]
    return and_(
        t.d.c.workspace_id == subject.workspace_id,
        t.v.c.document_id == t.d.c.id,
        or_(*rules),
    )


def readable_versions(subject: Subject, action: Action) -> Select:
    d, v = Document.__table__, _versions()
    return (
        select(v.c.id)
        .select_from(v.join(d, v.c.document_id == d.c.id))
        .where(read_clause(subject, action, d, v))
    )


def _versions():
    return DocumentVersion.__table__


# --- Single decisions ----------------------------------------------------------------------


def _inputs(subject: Subject, action: Action, doc: DocumentState | None, **extra) -> dict:
    inputs: dict[str, Any] = {
        "action": str(action),
        "role": subject.role,
        "channel": subject.channel,
        "group_ids": sorted(str(g) for g in subject.group_ids),
    }
    if subject.token_id:
        inputs["token_id"] = str(subject.token_id)
    if doc is not None:
        inputs.update(
            document_id=str(doc.document_id),
            author=doc.author_id == subject.user_id,
            has_serving=doc.has_serving,
            has_pending=doc.has_pending,
            withdrawn=doc.withdrawn,
        )
    inputs.update({k: str(v) for k, v in extra.items() if v is not None})
    return inputs


def decide(
    subject: Subject,
    action: Action,
    document: DocumentState | None = None,
    *,
    target_group_id: UUID | None = None,
    target_role: Role | None = None,
) -> Decision:
    inputs = _inputs(
        subject, action, document, target_group_id=target_group_id, target_role=target_role
    )

    def deny(rule: str) -> Decision:
        return Decision(False, rule, inputs)

    # R3: nothing crosses a workspace boundary, and non-members can do nothing.
    if subject.role is None or (document and document.workspace_id != subject.workspace_id):
        return deny("workspace.not_member")
    # R33: MCP is retrieval-only, whatever the user's role.
    if subject.channel == "mcp" and action is not Action.RETRIEVE:
        return deny("channel.mcp_retrieval_only")

    rule = _rule_for(subject, action, document, target_group_id, target_role)
    return Decision(True, rule, inputs) if rule else deny(f"{action}.not_permitted")


def _rule_for(
    s: Subject,
    action: Action,
    doc: DocumentState | None,
    target_group_id: UUID | None,
    target_role: Role | None,
) -> str | None:
    """Return the rule id that allows the action, or None. Follows the role capabilities table."""
    manager = s.role in MANAGERS
    owner = s.role == "owner"

    if action in READ_RULES:
        assert doc is not None
        for name, terms in READ_RULES[action]:
            if all(term.holds(s, doc) for term in terms):
                return name
        return None

    if action is Action.CREATE_DOCUMENT:
        return "create.member"  # R15: any member adds documents (as drafts)
    if action is Action.LIST_DOCUMENTS:
        return "list.member"  # each row is then filtered by the VIEW read rules
    if action is Action.MANAGE_OWN_TOKENS:
        return "tokens.own"  # R22
    if action is Action.CREATE_WORKSPACE:
        return "workspace.create" if owner else None  # R4: the creator is the owner
    if action is Action.ACCEPT_INVITE:
        return "invite.accepted"  # recorded once the new membership exists (R6)
    if action in (
        Action.MANAGE_GROUPS,
        Action.VIEW_ROSTER,
        Action.VIEW_AUDIT,
        Action.REVOKE_INVITE,
    ):
        return f"{action}.manager" if manager else None  # R10, R30

    if action is Action.INVITE:  # R5: admins invite members; the owner invites both
        if target_role == "member" and manager:
            return "invite.manager"
        return "invite.owner" if target_role == "admin" and owner else None
    if action is Action.REMOVE_MEMBER:  # R7
        if target_role == "member" and manager:
            return "remove.manager"
        return "remove.owner" if target_role == "admin" and owner else None
    if action is Action.CHANGE_ROLE:  # R7: only the owner promotes or demotes admins
        return "role.owner" if owner and target_role in ("admin", "member") else None
    if action is Action.TRANSFER_OWNERSHIP:  # R8: to a current admin only
        return "transfer.owner" if owner and target_role == "admin" else None

    assert doc is not None, f"{action} needs a document"
    author = doc.author_id == s.user_id
    live = not doc.withdrawn

    if action is Action.EDIT:  # R17, R18: authors edit their own live documents
        return "edit.author" if author and live else None
    if action is Action.SHARE:  # R12
        if not live:
            return None
        if manager:
            return "share.manager"
        return "share.author" if author and target_group_id in s.group_ids else None
    if action is Action.UNSHARE:  # R12: authors may unshare from any group
        return "unshare.manager" if manager else ("unshare.author" if author else None)
    if action in (Action.APPROVE, Action.REJECT, Action.REVIEW):  # R15, R16, R31, R35
        return f"{action}.manager" if manager and live and doc.has_pending else None
    if action is Action.DISCARD:  # R31
        return "discard.author" if author and live and doc.has_pending else None
    if action is Action.WITHDRAW:  # R18
        if not live:
            return None
        return "withdraw.manager" if manager else ("withdraw.author" if author else None)
    if action is Action.REASSIGN_AUTHOR:  # R32
        return "reassign.manager" if manager else None
    raise ValueError(f"unhandled action {action}")
