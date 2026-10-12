"""Store debt calculation settings as ENUM on MySQL, as the model declares.

20260729_bankcalc added repayment_type, day_count_convention and
early_repayment_strategy as VARCHAR(24), while the model uses Enum. SQLite
keeps enums as VARCHAR anyway, but on MySQL `flask db check` reports drift.

Revision ID: 20261013_debt_enums
Revises: 20261012_dictionary_types
Create Date: 2026-10-13 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261013_debt_enums'
down_revision = '20261012_dictionary_types'
branch_labels = None
depends_on = None

COLUMNS = {
    'repayment_type': ('annuity', 'differentiated'),
    'day_count_convention': ('actual_year', 'fixed_365', 'fixed_366'),
    'early_repayment_strategy': ('reduce_term', 'reduce_payment'),
}


def _is_mysql():
    return op.get_bind().dialect.name in ('mysql', 'mariadb')


def _quoted(values):
    return ', '.join(f"'{value}'" for value in values)


def upgrade():
    if not _is_mysql():
        return
    bind = op.get_bind()
    for column, values in COLUMNS.items():
        invalid = bind.execute(sa.text(
            f'SELECT COUNT(*) FROM debts WHERE {column} IS NULL OR {column} NOT IN ({_quoted(values)})'
        )).scalar()
        if invalid:
            raise RuntimeError(
                f'debts.{column} has {invalid} row(s) outside {values}. '
                'Back up the database, fix those rows, and run the migration again.'
            )
    for column, values in COLUMNS.items():
        op.execute(f'ALTER TABLE debts MODIFY {column} ENUM({_quoted(values)}) NOT NULL')


def downgrade():
    if not _is_mysql():
        return
    for column in COLUMNS:
        op.execute(f'ALTER TABLE debts MODIFY {column} VARCHAR(24) NOT NULL')
