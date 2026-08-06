"""Add Telegram processed updates.

Revision ID: 20260729_tgupdates
Revises: 20260729_splitbuy
Create Date: 2026-07-29 16:45:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260729_tgupdates'
down_revision = '20260729_splitbuy'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade():
    if _table_exists('telegram_processed_updates'):
        return

    op.create_table(
        'telegram_processed_updates',
        sa.Column('update_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('update_id'),
    )


def downgrade():
    pass
