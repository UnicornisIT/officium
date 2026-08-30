"""Align integrity constraints and add indexes used by core queries.

Revision ID: 20260829_integrity
Revises: 20260824_combpay
Create Date: 2026-08-29 20:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260829_integrity'
down_revision = '20260824_combpay'
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _index_names(table_name):
    return {item['name'] for item in _inspector().get_indexes(table_name)}


def _has_foreign_key(table_name, columns, referred_table):
    expected = list(columns)
    return any(
        item.get('constrained_columns') == expected
        and item.get('referred_table') == referred_table
        for item in _inspector().get_foreign_keys(table_name)
    )


def _create_index_if_missing(name, table_name, columns, unique=False):
    if name not in _index_names(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def upgrade():
    bind = op.get_bind()
    tables = set(_inspector().get_table_names())

    if 'expenses' in tables and not _has_foreign_key('expenses', ['generated_from_id'], 'expenses'):
        if bind.dialect.name == 'sqlite':
            with op.batch_alter_table('expenses', recreate='always') as batch_op:
                batch_op.create_foreign_key(
                    'fk_expenses_generated_from_id',
                    'expenses',
                    ['generated_from_id'],
                    ['id'],
                    ondelete='SET NULL',
                )
        else:
            op.create_foreign_key(
                'fk_expenses_generated_from_id',
                'expenses',
                'expenses',
                ['generated_from_id'],
                ['id'],
                ondelete='SET NULL',
            )

    duplicate = bind.execute(sa.text(
        """
        SELECT user_id, monthly_group_id, generated_for_month
        FROM expenses
        WHERE monthly_group_id IS NOT NULL AND generated_for_month IS NOT NULL
        GROUP BY user_id, monthly_group_id, generated_for_month
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )).first()
    if duplicate:
        raise RuntimeError(
            'Duplicate monthly expense occurrences exist. Back up the database, '
            'resolve duplicates, and run the migration again.'
        )

    _create_index_if_missing(
        'uq_expenses_monthly_occurrence',
        'expenses',
        ['user_id', 'monthly_group_id', 'generated_for_month'],
        unique=True,
    )
    _create_index_if_missing('ix_expenses_user_date', 'expenses', ['user_id', 'expense_date'])
    _create_index_if_missing('ix_incomes_user_date', 'incomes', ['user_id', 'income_date'])
    _create_index_if_missing('ix_payments_debt_date', 'payments', ['debt_id', 'payment_date'])
    _create_index_if_missing(
        'ix_debts_user_status_due',
        'debts',
        ['user_id', 'status', 'next_payment_date'],
    )


def downgrade():
    for name, table_name in (
        ('ix_debts_user_status_due', 'debts'),
        ('ix_payments_debt_date', 'payments'),
        ('ix_incomes_user_date', 'incomes'),
        ('ix_expenses_user_date', 'expenses'),
        ('uq_expenses_monthly_occurrence', 'expenses'),
    ):
        if name in _index_names(table_name):
            op.drop_index(name, table_name=table_name)

    if _has_foreign_key('expenses', ['generated_from_id'], 'expenses'):
        if op.get_bind().dialect.name == 'sqlite':
            with op.batch_alter_table('expenses', recreate='always') as batch_op:
                batch_op.drop_constraint('fk_expenses_generated_from_id', type_='foreignkey')
        else:
            op.drop_constraint('fk_expenses_generated_from_id', 'expenses', type_='foreignkey')
