"""Add bank calculation settings.

Revision ID: 20260729_bankcalc
Revises: 20260729_paybreak
Create Date: 2026-07-29 13:05:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260729_bankcalc'
down_revision = '20260729_paybreak'
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
    debt_columns = [
        ('repayment_type', sa.String(length=24), False, 'annuity'),
        ('day_count_convention', sa.String(length=24), False, 'actual_year'),
        ('include_payment_day', sa.Boolean(), False, sa.false()),
        ('interest_period_start_date', sa.Date(), True, None),
        ('early_repayment_strategy', sa.String(length=24), False, 'reduce_term'),
        ('loan_term_months', sa.Integer(), True, None),
        ('monthly_fee_amount', sa.Numeric(12, 2), False, '0'),
        ('bank_remaining_amount', sa.Numeric(12, 2), True, None),
    ]
    for name, column_type, nullable, default in debt_columns:
        if _column_exists('debts', name):
            continue
        kwargs = {'nullable': nullable}
        if default is not None:
            kwargs['server_default'] = default
        op.add_column('debts', sa.Column(name, column_type, **kwargs))

    payment_columns = [
        ('fee_amount', sa.Numeric(12, 2), False, '0'),
        ('bank_remaining_after_payment', sa.Numeric(12, 2), True, None),
    ]
    for name, column_type, nullable, default in payment_columns:
        if _column_exists('payments', name):
            continue
        kwargs = {'nullable': nullable}
        if default is not None:
            kwargs['server_default'] = default
        op.add_column('payments', sa.Column(name, column_type, **kwargs))

    if op.get_bind().dialect.name not in ('sqlite',):
        for name, *_ in debt_columns:
            if name not in ('interest_period_start_date', 'loan_term_months', 'bank_remaining_amount'):
                op.alter_column('debts', name, server_default=None)
        op.alter_column('payments', 'fee_amount', server_default=None)


def downgrade():
    for name in ('bank_remaining_after_payment', 'fee_amount'):
        if _column_exists('payments', name):
            op.drop_column('payments', name)

    for name in (
        'bank_remaining_amount',
        'monthly_fee_amount',
        'loan_term_months',
        'early_repayment_strategy',
        'interest_period_start_date',
        'include_payment_day',
        'day_count_convention',
        'repayment_type',
    ):
        if _column_exists('debts', name):
            op.drop_column('debts', name)
