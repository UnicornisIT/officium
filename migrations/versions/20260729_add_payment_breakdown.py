"""Add principal and interest payment breakdown.

Revision ID: 20260729_paybreak
Revises: 20260729_earlypay
Create Date: 2026-07-29 12:45:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260729_paybreak'
down_revision = '20260729_earlypay'
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
    if not _column_exists('payments', 'principal_amount'):
        op.add_column(
            'payments',
            sa.Column('principal_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
        )
    if not _column_exists('payments', 'interest_amount'):
        op.add_column(
            'payments',
            sa.Column('interest_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
        )

    payments = sa.table(
        'payments',
        sa.column('amount', sa.Numeric(12, 2)),
        sa.column('principal_amount', sa.Numeric(12, 2)),
        sa.column('interest_amount', sa.Numeric(12, 2)),
    )
    op.execute(payments.update().values(principal_amount=payments.c.amount, interest_amount=0))

    if op.get_bind().dialect.name not in ('sqlite',):
        op.alter_column('payments', 'principal_amount', server_default=None)
        op.alter_column('payments', 'interest_amount', server_default=None)


def downgrade():
    if _column_exists('payments', 'interest_amount'):
        op.drop_column('payments', 'interest_amount')
    if _column_exists('payments', 'principal_amount'):
        op.drop_column('payments', 'principal_amount')
