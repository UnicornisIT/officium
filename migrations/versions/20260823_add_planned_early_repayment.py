"""Add planned early repayment settings to debts.

Revision ID: 20260823_earlyplan
Revises: 20260821_goalflow
Create Date: 2026-08-23 20:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260823_earlyplan'
down_revision = '20260821_goalflow'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column['name'] for column in inspector.get_columns(table_name)}


def upgrade():
    if not _column_exists('debts', 'early_repayment_enabled'):
        op.add_column(
            'debts',
            sa.Column('early_repayment_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not _column_exists('debts', 'planned_early_repayment_amount'):
        op.add_column(
            'debts',
            sa.Column('planned_early_repayment_amount', sa.Numeric(12, 2), nullable=True),
        )

    if op.get_bind().dialect.name != 'sqlite':
        op.alter_column('debts', 'early_repayment_enabled', server_default=None)


def downgrade():
    if _column_exists('debts', 'planned_early_repayment_amount'):
        op.drop_column('debts', 'planned_early_repayment_amount')
    if _column_exists('debts', 'early_repayment_enabled'):
        op.drop_column('debts', 'early_repayment_enabled')
