"""Persist monthly expense anchor days and future-setting revisions.

Revision ID: 20261009_monthly_settings
Revises: 20260829_integrity
Create Date: 2026-10-09 19:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261009_monthly_settings'
down_revision = '20260829_integrity'
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade():
    columns = {column['name'] for column in _inspector().get_columns('expenses')}
    if 'monthly_anchor_day' not in columns:
        op.add_column('expenses', sa.Column('monthly_anchor_day', sa.SmallInteger(), nullable=True))
    if 'monthly_settings_updated_at' not in columns:
        op.add_column('expenses', sa.Column('monthly_settings_updated_at', sa.DateTime(), nullable=True))

    index_names = {index['name'] for index in _inspector().get_indexes('expenses')}
    if 'ix_expenses_monthly_active' not in index_names:
        op.create_index(
            'ix_expenses_monthly_active',
            'expenses',
            ['is_monthly', 'user_id', 'monthly_group_id'],
            unique=False,
        )


def downgrade():
    raise RuntimeError(
        'Downgrading monthly expense settings is disabled to preserve recurring-series metadata.'
    )
