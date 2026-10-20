"""turn sources, so the purge reaches turns built on a summary; index summaries by conversation

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The summary versions a saved turn was sent with: a stale one deletes the turn (R28).
    op.create_table(
        "turn_sources",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="turn_sources_document_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="turn_sources_version_fkey",
        ),
        sa.PrimaryKeyConstraint("turn_id", "version_id"),
    )
    op.create_index("turn_sources_document_idx", "turn_sources", ["document_id"])
    # Conversation deletes (retention, member removal) cascade here without a full scan.
    op.create_index(
        "conversation_summaries_conversation_idx", "conversation_summaries", ["conversation_id"]
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON turn_sources TO lattice_app")


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only.")
