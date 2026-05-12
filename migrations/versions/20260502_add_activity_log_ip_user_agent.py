"""Add ip_address and user_agent to activity_logs

Revision ID: 20260502_log_ip_ua
Revises: 20260502_mortgage
Create Date: 2026-05-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260502_log_ip_ua'
down_revision = '20260502_mortgage'
branch_labels = None
depends_on = None


MYSQL_DEBT_TYPE_SQL = (
    "ALTER TABLE debts "
    "MODIFY debt_type ENUM('credit_card', 'split', 'mortgage') NOT NULL"
)


def is_mysql():
    return op.get_bind().dialect.name in ('mysql', 'mariadb')


def table_exists(table_name):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def column_exists(table_name, column_name):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not table_exists(table_name):
        return False
    return column_name in [col['name'] for col in inspector.get_columns(table_name)]


def upgrade():
    if table_exists('activity_logs'):
        if not column_exists('activity_logs', 'ip_address'):
            op.add_column(
                'activity_logs',
                sa.Column('ip_address', sa.String(length=100), nullable=True)
            )
        if not column_exists('activity_logs', 'user_agent'):
            op.add_column(
                'activity_logs',
                sa.Column('user_agent', sa.Text(), nullable=True)
            )

    if is_mysql() and table_exists('debts'):
        op.execute(MYSQL_DEBT_TYPE_SQL)


def downgrade():
    raise RuntimeError(
        'Dropping activity log columns is disabled to preserve existing data.'
    )
