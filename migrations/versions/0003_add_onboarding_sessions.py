"""add onboarding sessions

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16 20:04:14.126513
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "onboarding_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "IN_PROGRESS",
                "COMPLETED",
                "ABANDONED",
                name="onboardingstatus",
                native_enum=False,
                length=32,
            ),
            server_default="in_progress",
            nullable=False,
        ),
        sa.Column("questions_asked", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("overall_confidence", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_onboarding_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_onboarding_sessions")),
    )
    op.create_index(
        op.f("ix_onboarding_sessions_user_id"), "onboarding_sessions", ["user_id"], unique=False
    )
    op.create_index(
        "uq_onboarding_sessions_active_user",
        "onboarding_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_onboarding_sessions_active_user",
        table_name="onboarding_sessions",
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    op.drop_index(op.f("ix_onboarding_sessions_user_id"), table_name="onboarding_sessions")
    op.drop_table("onboarding_sessions")
