"""Add vacation pay income category.

Revision ID: 20260729_vacpay
Revises: 20260729_restaurants
Create Date: 2026-07-29 12:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260729_vacpay'
down_revision = '20260729_restaurants'
branch_labels = None
depends_on = None


MYSQL_INCOME_CATEGORY_SQL = (
    "ALTER TABLE incomes "
    "MODIFY category ENUM("
    "'salary', 'advance', 'side_job', 'debt_return', 'bonus', "
    "'scholarship', 'vacation_pay', 'other'"
    ") NOT NULL"
)


def _is_mysql():
    return op.get_bind().dialect.name in ('mysql', 'mariadb')


def _table_exists(table_name):
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade():
    if _is_mysql() and _table_exists('incomes'):
        op.execute(MYSQL_INCOME_CATEGORY_SQL)


def downgrade():
    raise RuntimeError(
        'Downgrading income categories would remove vacation pay support and may corrupt data.'
    )
