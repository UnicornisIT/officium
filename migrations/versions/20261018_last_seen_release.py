"""Remember which release notes each user has acknowledged.

``users.last_seen_release`` stays empty for every existing account, so each of
them sees the notes of the release that is running once (and only that one,
never a backlog of older releases); new accounts are created with the running
version and see nothing until the next release.

Revision ID: 20261018_last_seen_release
Revises: 20261017_pay_period_settings
Create Date: 2026-10-18 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261018_last_seen_release'
down_revision = '20261017_pay_period_settings'
branch_labels = None
depends_on = None


def _columns(table_name):
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return None
    return {column['name'] for column in inspector.get_columns(table_name)}


def upgrade():
    columns = _columns('users')
    if columns is not None and 'last_seen_release' not in columns:
        with op.batch_alter_table('users') as batch_op:
            batch_op.add_column(sa.Column('last_seen_release', sa.String(length=64), nullable=True))


def downgrade():
    columns = _columns('users')
    if columns is not None and 'last_seen_release' in columns:
        with op.batch_alter_table('users') as batch_op:
            batch_op.drop_column('last_seen_release')
