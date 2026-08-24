"""Add an optional first payment amount to debts.

Revision ID: 20260823_firstpay
Revises: 20260823_earlyplan
Create Date: 2026-08-23 23:55:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260823_firstpay'
down_revision = '20260823_earlyplan'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column['name'] for column in inspector.get_columns(table_name)}


def upgrade():
    if not _column_exists('debts', 'first_payment_amount'):
        op.add_column(
            'debts',
            sa.Column('first_payment_amount', sa.Numeric(12, 2), nullable=True),
        )


def downgrade():
    if _column_exists('debts', 'first_payment_amount'):
        op.drop_column('debts', 'first_payment_amount')
