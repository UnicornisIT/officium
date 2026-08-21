"""Add custom financial goals and their transaction ledgers.

Revision ID: 20260821_goals
Revises: 20260821_fundtx
Create Date: 2026-08-21 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260821_goals'
down_revision = '20260821_fundtx'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _table_exists('financial_goals'):
        op.create_table(
            'financial_goals',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('target_amount', sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column('monthly_contribution', sa.Numeric(precision=12, scale=2), nullable=False, server_default='0'),
            sa.Column('target_date', sa.Date(), nullable=True),
            sa.Column('note', sa.String(length=500), nullable=True),
            sa.Column('priority', sa.Integer(), nullable=False, server_default='2'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint('target_amount > 0', name='ck_financial_goal_target_positive'),
            sa.CheckConstraint('monthly_contribution >= 0', name='ck_financial_goal_monthly_nonnegative'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_financial_goals_user_priority', 'financial_goals', ['user_id', 'priority'])

    if not _table_exists('financial_goal_transactions'):
        op.create_table(
            'financial_goal_transactions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('goal_id', sa.Integer(), nullable=False),
            sa.Column('transaction_type', sa.Enum('deposit', 'withdrawal', name='financial_goal_transaction_type'), nullable=False),
            sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column('transaction_date', sa.Date(), nullable=False),
            sa.Column('comment', sa.String(length=255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint('amount > 0', name='ck_financial_goal_transaction_amount_positive'),
            sa.ForeignKeyConstraint(['goal_id'], ['financial_goals.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            'ix_financial_goal_transactions_goal_date',
            'financial_goal_transactions',
            ['goal_id', 'transaction_date'],
        )


def downgrade():
    pass
