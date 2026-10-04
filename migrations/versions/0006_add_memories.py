"""add memories

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-19 09:33:01.880101
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "memory_type",
            sa.Enum(
                "FACT",
                "GOAL",
                "PREFERENCE",
                "CONSTRAINT",
                "EVENT",
                "EXPERIENCE",
                "OBSERVATION",
                "PLAN",
                "TEMPORARY_CONTEXT",
                name="memorytype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "SUPERSEDED",
                "ARCHIVED",
                name="memorystatus",
                native_enum=False,
                length=32,
            ),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "durability",
            sa.Enum(
                "LONG_TERM",
                "SHORT_TERM",
                "TEMPORARY",
                name="memorydurability",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "CONVERSATION",
                "ONBOARDING",
                "MANUAL",
                name="memorysource",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("is_sensitive", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
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
            name=op.f("fk_memories_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["memories.id"],
            name=op.f("fk_memories_superseded_by_id_memories"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memories_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
    )
    op.create_index(
        "ix_memories_user_id_occurred_at", "memories", ["user_id", "occurred_at"], unique=False
    )
    op.create_index("ix_memories_user_id_status", "memories", ["user_id", "status"], unique=False)
    op.create_index(
        "ix_memories_user_id_type_status",
        "memories",
        ["user_id", "memory_type", "status"],
        unique=False,
    )
    op.create_table(
        "memory_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column(
            "change",
            sa.Enum(
                "CREATED",
                "UPDATED",
                "SUPERSEDED",
                "ARCHIVED",
                "REINFORCED",
                name="memorychange",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "SUPERSEDED",
                "ARCHIVED",
                name="memorystatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.id"],
            name=op.f("fk_memory_versions_memory_id_memories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_versions")),
    )
    op.create_index(
        "ix_memory_versions_memory_id_created_at",
        "memory_versions",
        ["memory_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_memory_versions_memory_id_created_at", table_name="memory_versions")
    op.drop_table("memory_versions")
    op.drop_index("ix_memories_user_id_type_status", table_name="memories")
    op.drop_index("ix_memories_user_id_status", table_name="memories")
    op.drop_index("ix_memories_user_id_occurred_at", table_name="memories")
    op.drop_table("memories")
