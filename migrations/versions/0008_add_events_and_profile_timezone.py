"""add events and profile timezone

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-19 13:34:50.979570
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("activity", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("memory_id", sa.Uuid(), nullable=True),
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
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_events_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.id"],
            name=op.f("fk_events_memory_id_memories"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_events_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(
        "ix_events_user_id_activity_occurred_at",
        "events",
        ["user_id", "activity", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_events_user_id_occurred_at", "events", ["user_id", "occurred_at"], unique=False
    )
    op.add_column(
        "profiles",
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("profiles", "timezone")
    op.drop_index("ix_events_user_id_occurred_at", table_name="events")
    op.drop_index("ix_events_user_id_activity_occurred_at", table_name="events")
    op.drop_table("events")
