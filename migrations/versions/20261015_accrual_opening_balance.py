"""Carry unpaid interest and penalty over a moved accrual start.

When the next payment date is moved forward, earlier cycles are closed;
the interest and penalty still unpaid on the new start date are kept in
these columns instead of being dropped.

Revision ID: 20261015_accrual_opening
Revises: 20261014_debt_accrual
Create Date: 2026-10-15 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261015_accrual_opening'
down_revision = '20261014_debt_accrual'
branch_labels = None
depends_on = None

COLUMNS = ('opening_interest_amount', 'opening_penalty_amount')


def _column_exists(table_name, column_name):
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column['name'] for column in inspector.get_columns(table_name)}


def upgrade():
    for name in COLUMNS:
        if not _column_exists('debts', name):
            op.add_column('debts', sa.Column(name, sa.Numeric(12, 2), nullable=False, server_default='0'))
    if op.get_bind().dialect.name not in ('sqlite',):
        for name in COLUMNS:
            op.alter_column('debts', name, server_default=None, existing_type=sa.Numeric(12, 2), existing_nullable=False)


def downgrade():
    for name in reversed(COLUMNS):
        if _column_exists('debts', name):
            op.drop_column('debts', name)
