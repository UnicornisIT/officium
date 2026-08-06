"""Add consumer credit debt type.

Revision ID: 20260729_conscred
Revises: 20260729_vacpay
Create Date: 2026-07-29 12:35:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260729_conscred'
down_revision = '20260729_vacpay'
branch_labels = None
depends_on = None


MYSQL_DEBT_TYPE_SQL = (
    "ALTER TABLE debts "
    "MODIFY debt_type ENUM("
    "'credit_card', 'consumer_credit', 'split', 'mortgage'"
    ") NOT NULL"
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
        'Downgrading debt types would remove consumer credit support and may corrupt data.'
    )
