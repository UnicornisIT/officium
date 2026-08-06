"""Add Telegram conversation states.

Revision ID: 20260729_tgstate
Revises: 20260729_tgupdates
Create Date: 2026-07-29 17:10:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260729_tgstate'
down_revision = '20260729_tgupdates'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade():
    if _table_exists('telegram_conversation_states'):
        return

    op.create_table(
        'telegram_conversation_states',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('flow', sa.String(length=30), nullable=False),
        sa.Column('step', sa.String(length=50), nullable=False),
        sa.Column('data', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('telegram_id', name='uq_telegram_conversation_states_telegram_id'),
    )
    op.create_index(
        'ix_telegram_conversation_states_telegram_id',
        'telegram_conversation_states',
        ['telegram_id'],
    )
    op.create_index(
        'ix_telegram_conversation_states_expires_at',
        'telegram_conversation_states',
        ['expires_at'],
    )


def downgrade():
    pass
