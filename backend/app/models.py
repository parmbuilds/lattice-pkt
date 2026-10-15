"""Database schema. Every tenant row carries workspace_id; cross-table references include it."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
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
    # The receipt the approver acknowledged when it raised flags (R9, KTD13).
    acknowledged_receipt_sha256: Mapped[str | None] = mapped_column(Text)
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


def _document_keys(table: str) -> tuple[ForeignKeyConstraint, ForeignKeyConstraint]:
    """Workspace-scoped references to a document and one of its versions."""
    return (
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name=f"{table}_document_fkey",
        ),
        ForeignKeyConstraint(
            ["document_id", "version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name=f"{table}_version_fkey",
        ),
    )


class Intake(Base):
    """Permanent, insert-only record of an upload attempt; holds no file bytes (R4, KTD13)."""

    __tablename__ = "intakes"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"))
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    detected_type: Mapped[str | None] = mapped_column(Text)
    parser: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    receipt: Mapped[dict | None] = mapped_column(JSONB)  # flags and counts only
    receipt_sha256: Mapped[str | None] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None]  # null on rejects
    version_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("outcome IN ('accepted', 'rejected')", name="intakes_outcome_check"),
        CheckConstraint("length(filename) <= 256", name="intakes_filename_check"),
        CheckConstraint("length(reason) <= 256", name="intakes_reason_check"),
        *_document_keys("intakes"),
        # Review, approve and document detail look intakes up by version; uploads by hash.
        Index("intakes_version_idx", "version_id"),
        Index("intakes_workspace_source_idx", "workspace_id", "source_sha256"),
    )


class IntakePayload(Base):
    """Purgeable part of an accepted intake: original bytes and hidden excerpts (KTD13)."""

    __tablename__ = "intake_payloads"
    intake_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("intakes.id"), primary_key=True)
    workspace_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    version_id: Mapped[uuid.UUID]
    original: Mapped[bytes] = mapped_column(LargeBinary)
    hidden_excerpts: Mapped[list] = mapped_column(JSONB)
    __table_args__ = (
        *_document_keys("intake_payloads"),
        Index("intake_payloads_document_idx", "document_id"),  # purge_document
    )


class Derivation(Base):
    """Ledger of one version's chunking and embedding at one pipeline config (KTD3, KTD10)."""

    __tablename__ = "derivations"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    version_id: Mapped[uuid.UUID]
    config_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'ready', 'failed')", name="derivations_status_check"
        ),
        UniqueConstraint("version_id", "config_hash", name="derivations_version_config_key"),
        *_document_keys("derivations"),
        Index("derivations_document_idx", "document_id"),  # purge_document
        # The worker's queue: most rows are ready, so claim reads only the rest.
        Index(
            "derivations_due_idx",
            "next_attempt_at",
            postgresql_where=text("status <> 'ready'"),
        ),
    )


class Chunk(Base):
    """A span of an immutable body plus its embedding; no text is stored (KTD5)."""

    __tablename__ = "chunks"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    version_id: Mapped[uuid.UUID]
    config_hash: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(384))
    __table_args__ = (
        *_document_keys("chunks"),
        ForeignKeyConstraint(
            ["version_id", "config_hash"],
            ["derivations.version_id", "derivations.config_hash"],
            ondelete="CASCADE",
            name="chunks_derivation_fkey",
        ),
        # Exact distance only (KTD6): this index serves the join, not the ranking.
        Index("chunks_version_config_idx", "version_id", "config_hash"),
        # Lets vector search compute distances for one workspace's chunks, not the whole table.
        Index("chunks_workspace_config_idx", "workspace_id", "config_hash"),
        Index("chunks_document_idx", "document_id"),  # purge_document
    )


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="entities_workspace_id_id_key"),
        Index("entities_workspace_name_key", "workspace_id", text("lower(name)"), unique=True),
    )


class EntityAlias(Base):
    __tablename__ = "entity_aliases"
    id: Mapped[uuid.UUID] = _id()
    workspace_id: Mapped[uuid.UUID]
    entity_id: Mapped[uuid.UUID]
    alias: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "entity_id"],
            ["entities.workspace_id", "entities.id"],
            ondelete="CASCADE",
            name="entity_aliases_entity_fkey",
        ),
        Index(
            "entity_aliases_workspace_alias_key", "workspace_id", text("lower(alias)"), unique=True
        ),
    )
