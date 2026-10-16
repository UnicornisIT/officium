"""Drop the goal operation links to incomes and expenses.

After 20261016_goal_transfer_data no goal or emergency fund operation points at
an Income or Expense, and the application no longer reads these columns.
Constraint names differ between databases created by migrations and by
db.create_all() (MySQL names unnamed foreign keys itself), so they are looked
up with the inspector instead of being hard-coded.

The downgrade restores the nullable columns with their constraints; the links
themselves stay empty.

Revision ID: 20261016_drop_goal_cashflow
Revises: 20261016_goal_transfer_data
Create Date: 2026-10-16 12:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261016_drop_goal_cashflow'
down_revision = '20261016_goal_transfer_data'
branch_labels = None
depends_on = None

TABLES = ('financial_goal_transactions', 'emergency_fund_transactions')
LINKS = (('expense_id', 'expenses'), ('income_id', 'incomes'))


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table_name):
    inspector = _inspector()
    if table_name not in inspector.get_table_names():
        return None
    return {column['name'] for column in inspector.get_columns(table_name)}


def _drop_mysql(table_name, column):
    inspector = _inspector()
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key['constrained_columns'] == [column] and foreign_key.get('name'):
            op.drop_constraint(foreign_key['name'], table_name, type_='foreignkey')
    # MySQL reports UNIQUE constraints as unique indexes as well; drop each name once.
    names = {item['name'] for item in inspector.get_unique_constraints(table_name) if item['column_names'] == [column]}
    names.update(
        item['name'] for item in inspector.get_indexes(table_name)
        if item['column_names'] == [column]
    )
    for name in sorted(filter(None, names)):
        op.drop_index(name, table_name=table_name)
    op.drop_column(table_name, column)


def upgrade():
    dialect = op.get_bind().dialect.name
    for table_name in TABLES:
        columns = _columns(table_name)
        if columns is None:
            continue
        present = [column for column, _ in LINKS if column in columns]
        if not present:
            continue
        if dialect == 'sqlite':
            # Batch mode rebuilds the table and leaves out constraints on dropped columns.
            with op.batch_alter_table(table_name, recreate='always') as batch_op:
                for column in present:
                    batch_op.drop_column(column)
        else:
            for column in present:
                _drop_mysql(table_name, column)


def downgrade():
    for table_name in TABLES:
        columns = _columns(table_name)
        if columns is None:
            continue
        missing = [(column, target) for column, target in LINKS if column not in columns]
        if not missing:
            continue
        with op.batch_alter_table(table_name) as batch_op:
            for column, target in missing:
                batch_op.add_column(sa.Column(column, sa.Integer(), nullable=True))
                batch_op.create_unique_constraint(f'uq_{table_name}_{column}', [column])
                batch_op.create_foreign_key(
                    f'fk_{table_name}_{column}', target, [column], ['id'], ondelete='SET NULL',
                )
