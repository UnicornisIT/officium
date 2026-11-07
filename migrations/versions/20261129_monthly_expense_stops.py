"""Сохранять месяц прекращения повторяющихся расходов отдельно от истории."""

from alembic import op
import sqlalchemy as sa


revision = "20261129_monthly_expense_stops"
down_revision = "20261128_payment_cascade"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "monthly_expense_stops",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("monthly_group_id", sa.String(36), primary_key=True),
        sa.Column("stopped_from", sa.Date(), nullable=False),
    )


def downgrade():
    op.drop_table("monthly_expense_stops")
