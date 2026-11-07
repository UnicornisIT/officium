"""Отличать автоматические копии расходов от подтверждённых записей."""

from alembic import op
import sqlalchemy as sa


revision = "20261130_expense_auto"
down_revision = "20261129_monthly_expense_stops"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "expenses",
        sa.Column(
            "monthly_is_automatic",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    # Older copies with an explicit edit are preserved conservatively.
    op.execute(
        sa.text(
            "UPDATE expenses SET monthly_is_automatic = true "
            "WHERE generated_from_id IS NOT NULL AND is_monthly = true "
            "AND monthly_settings_updated_at IS NOT NULL "
            "AND monthly_settings_updated_at < created_at"
        )
    )


def downgrade():
    op.drop_column("expenses", "monthly_is_automatic")
