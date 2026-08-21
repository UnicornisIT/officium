"""Add the emergency fund transaction ledger.

Revision ID: 20260821_fundtx
Revises: 20260821_finplan
Create Date: 2026-08-21 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260821_fundtx'
down_revision = '20260821_finplan'
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name):
    return table_name in _inspector().get_table_names()


def _column_exists(table_name, column_name):
    if not _table_exists(table_name):
        return False
    return column_name in {column['name'] for column in _inspector().get_columns(table_name)}


def upgrade():
    if not _table_exists('emergency_fund_transactions'):
        op.create_table(
            'emergency_fund_transactions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column(
                'transaction_type',
                sa.Enum('deposit', 'withdrawal', name='emergency_fund_transaction_type'),
                nullable=False,
            ),
            sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column('transaction_date', sa.Date(), nullable=False),
            sa.Column('comment', sa.String(length=255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint('amount > 0', name='ck_emergency_fund_transaction_amount_positive'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            'ix_emergency_fund_transactions_user_date',
            'emergency_fund_transactions',
            ['user_id', 'transaction_date'],
        )

    if _column_exists('financial_plan_preferences', 'emergency_fund_current'):
        op.execute(sa.text("""
            INSERT INTO emergency_fund_transactions
                (user_id, transaction_type, amount, transaction_date, comment, created_at)
            SELECT
                preference.user_id,
                'deposit',
                preference.emergency_fund_current,
                CURRENT_DATE,
                'Перенесенный остаток',
                CURRENT_TIMESTAMP
            FROM financial_plan_preferences AS preference
            WHERE preference.emergency_fund_current > 0
              AND NOT EXISTS (
                  SELECT 1
                  FROM emergency_fund_transactions AS fund_transaction
                  WHERE fund_transaction.user_id = preference.user_id
                    AND fund_transaction.comment = 'Перенесенный остаток'
              )
        """))
        op.drop_column('financial_plan_preferences', 'emergency_fund_current')


def downgrade():
    pass
