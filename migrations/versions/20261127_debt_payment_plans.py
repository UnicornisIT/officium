"""Store debt payment plans separately from recorded payments."""

import sqlalchemy as sa
from alembic import op

revision = "20261127_debt_payment_plans"
down_revision = "20261126_attachment_dedup"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "debt_payment_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "debt_id",
            sa.Integer(),
            sa.ForeignKey("debts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("comment", sa.Text()),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_debt_payment_plans_debt_id", "debt_payment_plans", ["debt_id"])


def downgrade():
    op.drop_table("debt_payment_plans")
