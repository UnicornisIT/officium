"""Remove settings that never affected the application.

default_currency only changed a label (all amounts are stored in roubles) and
overdue_after_date was never read; both are removed from the admin form.

Revision ID: 20261012_retire_settings
Revises: 20261012_impersonation
Create Date: 2026-10-12 23:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261012_retire_settings'
down_revision = '20261012_impersonation'
branch_labels = None
depends_on = None

RETIRED = {
    'default_currency': 'RUB',
    'overdue_after_date': 'true',
}

app_settings = sa.table(
    'app_settings',
    sa.column('key', sa.String(100)),
    sa.column('value', sa.Text()),
)


def upgrade():
    op.execute(app_settings.delete().where(app_settings.c.key.in_(list(RETIRED))))


def downgrade():
    # Restore the previous defaults so the old settings form shows its values.
    bind = op.get_bind()
    existing = {
        row[0]
        for row in bind.execute(sa.select(app_settings.c.key).where(app_settings.c.key.in_(list(RETIRED))))
    }
    rows = [{'key': key, 'value': value} for key, value in RETIRED.items() if key not in existing]
    if rows:
        op.bulk_insert(app_settings, rows)
