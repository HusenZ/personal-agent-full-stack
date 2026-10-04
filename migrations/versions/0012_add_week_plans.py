"""add week plans

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-03 05:58:29.719473
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('week_plans',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('starts_on', sa.Date(), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('blocks', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evidence_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('prompt_name', sa.String(length=100), nullable=False),
    sa.Column('prompt_version', sa.String(length=20), nullable=False),
    sa.Column('rejected_block_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_week_plans_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_week_plans'))
    )
    op.create_index('ix_week_plans_user_id_created_at', 'week_plans', ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_week_plans_user_id_created_at', table_name='week_plans')
    op.drop_table('week_plans')
