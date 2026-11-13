"""add run failure diagnostics

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-12 06:58:41.076547
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no default: a metadata-only change, whatever the table's size.
    op.add_column(
        "agent_runs",
        sa.Column("error_chain", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("agent_runs", sa.Column("error_location", sa.String(length=300), nullable=True))

    # Every chat turn writes a run, so the index is built without blocking writes.
    # CONCURRENTLY cannot run inside a transaction.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_agent_runs_started_at",
            "agent_runs",
            ["started_at"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_agent_runs_started_at",
            table_name="agent_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_column("agent_runs", "error_location")
    op.drop_column("agent_runs", "error_chain")
