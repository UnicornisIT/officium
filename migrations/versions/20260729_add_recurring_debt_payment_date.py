"""Add recurring debt payment date flag.

Revision ID: 20260729_debtrecur
Revises: 20260729_conscred
Create Date: 2026-07-29 13:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260729_debtrecur'
down_revision = '20260729_conscred'
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
    if _column_exists('debts', 'is_payment_recurring'):
        return
    op.add_column(
        'debts',
        sa.Column('is_payment_recurring', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    if op.get_bind().dialect.name not in ('sqlite',):
        op.alter_column('debts', 'is_payment_recurring', server_default=None)


def downgrade():
    if _column_exists('debts', 'is_payment_recurring'):
        op.drop_column('debts', 'is_payment_recurring')
