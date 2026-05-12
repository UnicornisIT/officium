"""Ensure debt_type enum supports mortgage

Revision ID: 20260503_debt_type
Revises: 20260502_log_ip_ua
Create Date: 2026-05-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260503_debt_type'
down_revision = '20260502_log_ip_ua'
branch_labels = None
depends_on = None


MYSQL_DEBT_TYPE_SQL = (
    "ALTER TABLE debts "
    "MODIFY debt_type ENUM('credit_card', 'split', 'mortgage') NOT NULL"
)


def _is_mysql():
    return op.get_bind().dialect.name in ('mysql', 'mariadb')


def _table_exists(table_name):
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade():
    if _is_mysql() and _table_exists('debts'):
        op.execute(MYSQL_DEBT_TYPE_SQL)


def downgrade():
    raise RuntimeError(
        'Downgrading debt_type would remove mortgage support and may corrupt data.'
    )
