"""add memory keyword search

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-27 19:00:03.752181
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_memories_search_vector",
        "memories",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_memories_search_vector", table_name="memories", postgresql_using="gin")
    op.drop_column("memories", "search_vector")
