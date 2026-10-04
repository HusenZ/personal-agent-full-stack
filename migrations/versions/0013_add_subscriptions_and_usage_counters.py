"""add subscriptions and usage counters

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-04 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "plan",
            sa.Enum("FREE", "PRO", name="plan", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
            ["user_id"],
            ["users.id"],
            name=op.f("fk_subscriptions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_subscriptions")),
    )
    op.create_table(
        "usage_counters",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "feature",
            sa.Enum(
                "CHAT_MESSAGE",
                "WEEK_PLAN",
                "ANALYSIS",
                name="feature",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_usage_counters_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "feature", "period_start", name=op.f("pk_usage_counters")
        ),
    )


def downgrade() -> None:
    op.drop_table("usage_counters")
    op.drop_table("subscriptions")
