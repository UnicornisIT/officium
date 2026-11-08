"""Add explicit card conditions; leave legacy contracts unknown."""

from alembic import op
import sqlalchemy as sa


revision = "20261202_card_grace"
down_revision = "20261201_debt_opening"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("debts", sa.Column("grace_policy", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("debts", "grace_policy")
