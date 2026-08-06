"""Add restaurants expense category.

Revision ID: 20260729_restaurants
Revises: 20260517_merge_heads
Create Date: 2026-07-29 10:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260729_restaurants'
down_revision = '20260517_merge_heads'
branch_labels = None
depends_on = None


MYSQL_EXPENSE_CATEGORY_SQL = (
    "ALTER TABLE expenses "
    "MODIFY category ENUM("
    "'products', 'transport', 'communication', 'rent', 'loans', "
    "'restaurants', 'entertainment', 'health', 'education', 'clothing', "
    "'subscriptions', 'other'"
    ") NOT NULL"
)


def _is_mysql():
    return op.get_bind().dialect.name in ('mysql', 'mariadb')


def _table_exists(table_name):
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade():
    if _is_mysql() and _table_exists('expenses'):
        op.execute(MYSQL_EXPENSE_CATEGORY_SQL)


def downgrade():
    raise RuntimeError(
        'Downgrading expense categories would remove restaurants support and may corrupt data.'
    )
