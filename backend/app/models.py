"""Database schema. Every tenant row carries workspace_id; cross-table references include it."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {uuid.UUID: UUID(as_uuid=True), datetime: DateTime(timezone=True)}


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))


def _created() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("users_email_key", text("lower(email)"), unique=True),)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    verifier: Mapped[bytes] = mapped_column(LargeBinary)
    csrf_token: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Membership(Base):
    __tablename__ = "memberships"
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="memberships_role_check"),
        # R4: at most one owner per workspace.
        Index(
            "memberships_one_owner",
            "workspace_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
    )


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="groups_workspace_id_id_key"),
        Index("groups_workspace_name_key", "workspace_id", text("lower(name)"), unique=True),
    )


class GroupMember(Base):
    __tablename__ = "group_members"
    workspace_id: Mapped[uuid.UUID]
    group_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "group_id"],
            ["groups.workspace_id", "groups.id"],
            ondelete="CASCADE",
            name="group_members_group_fkey",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            ondelete="CASCADE",
            name="group_members_membership_fkey",
        ),
    )


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    serving_version_id: Mapped[uuid.UUID | None]
    pending_version_id: Mapped[uuid.UUID | None]
    withdrawn_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="documents_workspace_id_id_key"),
        # Pointers may only reference this document's own versions.
        ForeignKeyConstraint(
            ["id", "serving_version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="documents_serving_version_fkey",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["id", "pending_version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="documents_pending_version_fkey",
            use_alter=True,
        ),
    )


class DocumentVersion(Base):
    """Immutable: a trigger rejects every UPDATE and DELETE (KTD4)."""

    __tablename__ = "document_versions"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created()
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', title), 'A') || "
            "setweight(to_tsvector('english', body), 'B')",
            persisted=True,
        ),
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="document_versions_document_fkey",
        ),
        UniqueConstraint("document_id", "id", name="document_versions_document_id_id_key"),
        Index("document_versions_search_idx", "search_vector", postgresql_using="gin"),
    )


class VersionDecision(Base):
    """Insert-only record of approve, reject and discard decisions on draft versions."""

    __tablename__ = "version_decisions"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    version_id: Mapped[uuid.UUID]
    decision: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected', 'discarded')", name="version_decisions_check"
        ),
        ForeignKeyConstraint(
            ["document_id", "version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="version_decisions_version_fkey",
        ),
        # A draft is decided at most once.
        UniqueConstraint("version_id", name="version_decisions_version_id_key"),
    )


class DocumentGrant(Base):
    __tablename__ = "document_grants"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    group_id: Mapped[uuid.UUID | None]
    user_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("num_nonnulls(group_id, user_id) = 1", name="document_grants_subject"),
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            ondelete="CASCADE",
            name="document_grants_document_fkey",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "group_id"],
            ["groups.workspace_id", "groups.id"],
            ondelete="CASCADE",
            name="document_grants_group_fkey",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            ondelete="CASCADE",
            name="document_grants_membership_fkey",
        ),
        UniqueConstraint("document_id", "group_id", name="document_grants_group_key"),
        UniqueConstraint("document_id", "user_id", name="document_grants_user_key"),
    )


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(Text)
    inviter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    verifier: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    accepted_at: Mapped[datetime | None]
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (CheckConstraint("role IN ('admin', 'member')", name="invites_role_check"),)


class AccessToken(Base):
    """Personal access token; soft-revoked, never deleted, so audit rows keep their reference."""

    __tablename__ = "access_tokens"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text)
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    verifier: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]


class AuditEvent(Base):
    """Append-only to the runtime role: INSERT and SELECT only, plus a guard trigger (KTD8)."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"))
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(Text)
    token_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[str | None] = mapped_column(Text)
    inputs: Mapped[dict | None] = mapped_column(JSONB)
    document_id: Mapped[uuid.UUID | None]
    version_id: Mapped[uuid.UUID | None]
    returned_version_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint("channel IN ('ui', 'mcp', 'system')", name="audit_events_channel_check"),
        CheckConstraint("outcome IN ('allow', 'deny')", name="audit_events_outcome_check"),
        Index("audit_events_workspace_time_idx", "workspace_id", "occurred_at"),
    )
