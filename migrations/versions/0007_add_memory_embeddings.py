"""add memory embeddings

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-19 09:56:48.577386
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No ANN index yet: per-user exact scans are fast and avoid filtered-recall issues.
    op.add_column(
        "memories", sa.Column("embedding", pgvector.sqlalchemy.Vector(1536), nullable=True)
    )
    op.add_column("memories", sa.Column("embedding_model", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("memories", "embedding_model")
    op.drop_column("memories", "embedding")
