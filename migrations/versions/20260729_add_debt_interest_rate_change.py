"""Add scheduled debt interest rate change.

Revision ID: 20260729_debtrate
Revises: 20260729_debtrecur
Create Date: 2026-07-29 13:35:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260729_debtrate'
down_revision = '20260729_debtrecur'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _column_exists(table_name, column_name):
    if not _table_exists(table_name):
        return False
    inspector = sa.inspect(op.get_bind())
    return column_name in {column['name'] for column in inspector.get_columns(table_name)}


def upgrade():
    if not _column_exists('debts', 'interest_rate_after_change'):
        op.add_column('debts', sa.Column('interest_rate_after_change', sa.Numeric(5, 2), nullable=True))
    if not _column_exists('debts', 'interest_rate_change_date'):
        op.add_column('debts', sa.Column('interest_rate_change_date', sa.Date(), nullable=True))


def downgrade():
    if _column_exists('debts', 'interest_rate_change_date'):
        op.drop_column('debts', 'interest_rate_change_date')
    if _column_exists('debts', 'interest_rate_after_change'):
        op.drop_column('debts', 'interest_rate_after_change')
