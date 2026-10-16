"""indexes for purges, intake lookups and the derivation queue

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = [
    ("intakes_version_idx", "intakes", ["version_id"]),
    ("intakes_workspace_source_idx", "intakes", ["workspace_id", "source_sha256"]),
    ("intake_payloads_document_idx", "intake_payloads", ["document_id"]),
    ("derivations_document_idx", "derivations", ["document_id"]),
    ("chunks_document_idx", "chunks", ["document_id"]),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)
    op.create_index(
        "derivations_due_idx",
        "derivations",
        ["next_attempt_at"],
        postgresql_where=sa.text("status <> 'ready'"),
    )


def downgrade() -> None:
    op.drop_index("derivations_due_idx", table_name="derivations")
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
