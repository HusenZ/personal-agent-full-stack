"""add agent runs

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-01 04:53:20.242402
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum("CONVERSATION_TURN", name="runkind", native_enum=False, length=50),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("COMPLETED", "FAILED", name="runstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("retrieval_attempts", sa.Integer(), nullable=False),
        sa.Column("generation_attempts", sa.Integer(), nullable=False),
        sa.Column("grounded", sa.Boolean(), nullable=True),
        sa.Column("degraded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("failed_node", sa.String(length=50), nullable=True),
        sa.Column(
            "steps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_agent_runs_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_agent_runs_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index(
        "ix_agent_runs_user_id_started_at", "agent_runs", ["user_id", "started_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_user_id_started_at", table_name="agent_runs")
    op.drop_table("agent_runs")
