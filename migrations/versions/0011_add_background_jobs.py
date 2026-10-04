"""add background jobs

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-01 05:08:16.831011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("MEMORY_EXTRACTION", name="jobkind", native_enum=False, length=50),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "SUCCEEDED",
                "FAILED",
                name="jobstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("last_error_type", sa.String(length=100), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_background_jobs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_jobs")),
    )
    op.create_index(
        "ix_background_jobs_claimable",
        "background_jobs",
        ["run_after"],
        unique=False,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_background_jobs_claimable",
        table_name="background_jobs",
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )
    op.drop_table("background_jobs")
