"""Merge Google OAuth and monthly expenses migration branches.

Revision ID: 20260517_merge_heads
Revises: e49a6c3dc4b8, 20260517_monthly_expenses
Create Date: 2026-05-17 21:00:00.000000
"""


# revision identifiers, used by Alembic.
revision = '20260517_merge_heads'
down_revision = ('e49a6c3dc4b8', '20260517_monthly_expenses')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError(
        'Downgrading the migration merge point is disabled to preserve history.'
    )
