"""Store an independent opening principal without rewriting legacy history."""

from alembic import op
import sqlalchemy as sa


revision = '20261201_debt_opening'
down_revision = '20261130_expense_auto'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('debts', sa.Column('opening_principal_amount', sa.Numeric(12, 2), nullable=True))
    op.add_column('debts', sa.Column('payment_day', sa.SmallInteger(), nullable=True))


def downgrade():
    op.drop_column('debts', 'payment_day')
    op.drop_column('debts', 'opening_principal_amount')
