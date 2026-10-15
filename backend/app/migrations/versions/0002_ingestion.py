"""document ingestion: intakes, payloads, derivations, chunks, entities, acknowledgement

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15

The vector extension is installed by the superuser in db/init/01-roles.sql because pgvector
is not a trusted extension; this migration only checks that it exists (KTD2).

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
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


def upgrade() -> None:
    """Upgrade schema."""
    installed = op.get_bind().execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    )
    if installed.first() is None:
        raise RuntimeError(
            "The pgvector extension is missing. As the PostgreSQL superuser, run "
            "CREATE EXTENSION vector; in this database (see db/init/01-roles.sql), then migrate."
        )

    op.add_column(
        "version_decisions", sa.Column("acknowledged_receipt_sha256", sa.Text(), nullable=True)
    )
    op.create_table(
        "intakes",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("detected_type", sa.Text(), nullable=True),
        sa.Column("parser", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("receipt", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("receipt_sha256", sa.Text(), nullable=True),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("version_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("outcome IN ('accepted', 'rejected')", name="intakes_outcome_check"),
        sa.CheckConstraint("length(filename) <= 256", name="intakes_filename_check"),
        sa.CheckConstraint("length(reason) <= 256", name="intakes_reason_check"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        *_document_keys("intakes"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "intake_payloads",
        sa.Column("intake_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("original", sa.LargeBinary(), nullable=False),
        sa.Column("hidden_excerpts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["intake_id"], ["intakes.id"]),
        *_document_keys("intake_payloads"),
        sa.PrimaryKeyConstraint("intake_id"),
    )
    op.create_table(
        "derivations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed')", name="derivations_status_check"
        ),
        *_document_keys("derivations"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "config_hash", name="derivations_version_config_key"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("embedding", VECTOR(384), nullable=False),
        *_document_keys("chunks"),
        sa.ForeignKeyConstraint(
            ["version_id", "config_hash"],
            ["derivations.version_id", "derivations.config_hash"],
            name="chunks_derivation_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Exact distance only (KTD6): this index serves the join, not the ranking.
    op.create_index("chunks_version_config_idx", "chunks", ["version_id", "config_hash"])
    # Lets vector search compute distances for one workspace's chunks, not the whole table.
    op.create_index("chunks_workspace_config_idx", "chunks", ["workspace_id", "config_hash"])
    op.create_table(
        "entities",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="entities_workspace_id_id_key"),
    )
    op.create_index(
        "entities_workspace_name_key",
        "entities",
        ["workspace_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "entity_id"],
            ["entities.workspace_id", "entities.id"],
            name="entity_aliases_entity_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "entity_aliases_workspace_alias_key",
        "entity_aliases",
        ["workspace_id", sa.literal_column("lower(alias)")],
        unique=True,
    )

    # The intake record is permanent (R4); everything derived from a version is purgeable.
    op.execute("GRANT SELECT, INSERT ON intakes TO lattice_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON intake_payloads, derivations, chunks, "
        "entities, entity_aliases TO lattice_app"
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only.")
