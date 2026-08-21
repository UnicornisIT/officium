"""Add per-user financial plan preferences.

Revision ID: 20260821_finplan
Revises: 20260729_tgstate
Create Date: 2026-08-21 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260821_finplan'
down_revision = '20260729_tgstate'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade():
    if _table_exists('financial_plan_preferences'):
        return

    op.create_table(
        'financial_plan_preferences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('living_minimum', sa.Numeric(precision=12, scale=2), nullable=False, server_default='20000'),
        sa.Column('desired_monthly_savings', sa.Numeric(precision=12, scale=2), nullable=False, server_default='5000'),
        sa.Column('emergency_fund_target_amount', sa.Numeric(precision=12, scale=2), nullable=False, server_default='30000'),
        sa.Column(
            'emergency_fund_target_mode',
            sa.Enum('fixed', 'one_month', 'three_months', name='financial_plan_target_mode'),
            nullable=False,
            server_default='fixed',
        ),
        sa.Column(
            'strategy',
            sa.Enum('safe', 'balanced', 'aggressive', name='financial_plan_strategy'),
            nullable=False,
            server_default='balanced',
        ),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_financial_plan_preferences_user_id'),
    )


def downgrade():
    pass
