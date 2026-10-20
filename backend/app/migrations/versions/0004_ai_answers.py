"""written answers in Ask: AI settings, conversations, turns and summaries

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _document_keys(table: str) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name=f"{table}_document_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name=f"{table}_version_fkey",
        ),
    ]


def _conversation_key(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", "conversation_id"],
        ["conversations.workspace_id", "conversations.id"],
        ondelete="CASCADE",
        name=f"{table}_conversation_fkey",
    )


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "ai_settings",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("encrypted_key", sa.LargeBinary(), nullable=True),
        sa.Column("key_status", sa.Text(), server_default=sa.text("'ok'"), nullable=False),
        sa.Column("retention_days", sa.Integer(), server_default=sa.text("30"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("key_status IN ('ok', 'rejected')", name="ai_settings_key_status_check"),
        sa.CheckConstraint("retention_days BETWEEN 1 AND 365", name="ai_settings_retention_check"),
        sa.CheckConstraint(
            "num_nulls(provider_id, model, encrypted_key) IN (0, 3)", name="ai_settings_provider"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    op.create_table(
        "conversations",
        _id(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        _created(),
        sa.Column(
            "last_turn_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("in_flight_turn", sa.Integer(), nullable=True),
        sa.Column("in_flight_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("left_out", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("summary_dropped", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            ondelete="CASCADE",
            name="conversations_membership_fkey",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="conversations_workspace_id_id_key"),
    )
    op.create_index(
        "conversations_user_idx", "conversations", ["workspace_id", "user_id", "last_turn_at"]
    )
    op.create_table(
        "turns",
        _id(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("sentences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        _created(),
        sa.CheckConstraint(
            "outcome IN ('answered', 'partly_supported', 'not_covered')", name="turns_outcome_check"
        ),
        _conversation_key("turns"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "ordinal", name="turns_conversation_ordinal_key"),
    )
    op.create_table(
        "turn_passages",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="CASCADE"),
        *_document_keys("turn_passages"),
        sa.PrimaryKeyConstraint("turn_id", "ordinal"),
    )
    op.create_index("turn_passages_document_idx", "turn_passages", ["document_id"])
    op.create_table(
        "turn_sources",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="CASCADE"),
        *_document_keys("turn_sources"),
        sa.PrimaryKeyConstraint("turn_id", "version_id"),
    )
    op.create_index("turn_sources_document_idx", "turn_sources", ["document_id"])
    op.create_table(
        "conversation_summaries",
        _id(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("covers_through", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        _created(),
        _conversation_key("conversation_summaries"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "conversation_summaries_conversation_idx", "conversation_summaries", ["conversation_id"]
    )
    op.create_table(
        "summary_sources",
        sa.Column("summary_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["summary_id"], ["conversation_summaries.id"], ondelete="CASCADE"),
        *_document_keys("summary_sources"),
        sa.PrimaryKeyConstraint("summary_id", "version_id"),
    )
    op.create_index("summary_sources_document_idx", "summary_sources", ["document_id"])

    # Saved conversation text is never rewritten, only deleted whole (KTD4).
    for table in ("turns", "conversation_summaries"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_modification()"
        )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ai_settings, conversations TO lattice_app")
    op.execute(
        "GRANT SELECT, INSERT, DELETE ON turns, turn_passages, turn_sources, "
        "conversation_summaries, summary_sources TO lattice_app"
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only.")
