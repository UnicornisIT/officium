"""Add the manual financial plan mark of an income.

``incomes.pay_period_mode`` is empty for every existing income, so the
category keeps deciding which incomes are main payments for the plan.

Revision ID: 20261017_pay_period_settings
Revises: 20261016_drop_goal_cashflow
Create Date: 2026-10-17 15:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261017_pay_period_settings'
down_revision = '20261016_drop_goal_cashflow'
branch_labels = None
depends_on = None


def _columns(table_name):
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return None
    return {column['name'] for column in inspector.get_columns(table_name)}


def upgrade():
    columns = _columns('incomes')
    if columns is not None and 'pay_period_mode' not in columns:
        with op.batch_alter_table('incomes') as batch_op:
            batch_op.add_column(sa.Column('pay_period_mode', sa.String(length=8), nullable=True))


def downgrade():
    columns = _columns('incomes')
    if columns is not None and 'pay_period_mode' in columns:
        with op.batch_alter_table('incomes') as batch_op:
            batch_op.drop_column('pay_period_mode')
