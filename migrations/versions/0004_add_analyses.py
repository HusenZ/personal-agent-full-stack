"""add analyses

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-19 08:17:15.276222
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("claims", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("overall_confidence", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("prompt_name", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=20), nullable=False),
        sa.Column("generation_attempts", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "rejected_claim_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("degraded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
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
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_analyses_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyses")),
    )
    op.create_index(
        "ix_analyses_user_id_created_at", "analyses", ["user_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_analyses_user_id_created_at", table_name="analyses")
    op.drop_table("analyses")
