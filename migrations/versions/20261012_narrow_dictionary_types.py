"""Keep only dictionary types that the debt form actually uses.

debt_type and status are fixed enums used by calculations, and debts have no
category field, so those dictionaries could never affect the application.

Revision ID: 20261012_dictionary_types
Revises: 20261012_retire_settings
Create Date: 2026-10-12 23:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261012_dictionary_types'
down_revision = '20261012_retire_settings'
branch_labels = None
depends_on = None

RETIRED_TYPES = ('debt_type', 'debt_category', 'status')
KEPT_TYPES = ('bank', 'comment_template', 'interest_rate', 'product_type')
PREVIOUS_TYPES = ('bank', 'debt_type', 'debt_category', 'status', 'comment_template', 'interest_rate', 'product_type')

dictionary_entries = sa.table(
    'dictionary_entries',
    sa.column('dictionary_type', sa.String(32)),
)


def _is_mysql():
    return op.get_bind().dialect.name in ('mysql', 'mariadb')


def _enum_sql(values):
    quoted = ', '.join(f"'{value}'" for value in values)
    return f'ALTER TABLE dictionary_entries MODIFY dictionary_type ENUM({quoted}) NOT NULL'


def upgrade():
    op.execute(dictionary_entries.delete().where(dictionary_entries.c.dictionary_type.in_(RETIRED_TYPES)))
    # SQLite stores the Enum as VARCHAR without a CHECK constraint, so only
    # MySQL/MariaDB needs the column definition narrowed.
    if _is_mysql():
        op.execute(_enum_sql(KEPT_TYPES))


def downgrade():
    # Widening is safe; entries deleted by upgrade are not restored.
    if _is_mysql():
        op.execute(_enum_sql(PREVIOUS_TYPES))
