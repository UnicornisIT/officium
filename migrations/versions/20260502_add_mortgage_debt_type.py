"""Add mortgage value to debt_type enum

Revision ID: 20260502_add_mortgage_debt_type
Revises: 73459c8513a1
Create Date: 2026-05-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260502_add_mortgage_debt_type'
down_revision = '73459c8513a1'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE debts MODIFY debt_type ENUM('credit_card', 'split', 'mortgage') NOT NULL"
    )


def downgrade():
    op.execute(
        "ALTER TABLE debts MODIFY debt_type ENUM('credit_card', 'split') NOT NULL"
    )
