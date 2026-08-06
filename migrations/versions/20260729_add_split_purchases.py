"""Add split purchases.

Revision ID: 20260729_splitbuy
Revises: 20260729_bankcalc
Create Date: 2026-07-29 16:20:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260729_splitbuy'
down_revision = '20260729_bankcalc'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade():
    if _table_exists('split_purchases'):
        return

    op.create_table(
        'split_purchases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('debt_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=True),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('purchase_date', sa.Date(), nullable=False),
        sa.Column('installments_count', sa.Integer(), nullable=False, server_default='4'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['debt_id'], ['debts.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    pass
