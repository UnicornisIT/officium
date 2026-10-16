"""Delete incomes and expenses that were created automatically from goal operations.

Goal and emergency fund operations are transfers of the user's own money and
no longer create an Income (category 'goal_withdrawal') or an Expense
(category 'savings'). This migration removes exactly the records linked
through financial_goal_transactions.expense_id/income_id and
emergency_fund_transactions.expense_id/income_id, after clearing those links.
Records are never selected by category or text: an expense entered by hand
with category 'savings' or an income with 'goal_withdrawal' stays.

The data cannot be restored by the downgrade: it only keeps the schema
consistent. To get the deleted records back, restore the database backup
that officium-update creates before running migrations.

Revision ID: 20261016_goal_transfer_data
Revises: 20261015_accrual_opening
Create Date: 2026-10-16 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261016_goal_transfer_data'
down_revision = '20261015_accrual_opening'
branch_labels = None
depends_on = None

LINK_TABLES = ('financial_goal_transactions', 'emergency_fund_transactions')
LINKS = (('expense_id', 'expenses'), ('income_id', 'incomes'))
CHUNK_SIZE = 500


def _columns(table_name):
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column['name'] for column in inspector.get_columns(table_name)}


def _chunks(values):
    values = sorted(values)
    for start in range(0, len(values), CHUNK_SIZE):
        yield values[start:start + CHUNK_SIZE]


def _linked_ids(bind, column):
    ids = set()
    for table_name in LINK_TABLES:
        if column not in _columns(table_name):
            continue
        ids.update(
            row[0] for row in bind.execute(sa.text(
                f'SELECT {column} FROM {table_name} WHERE {column} IS NOT NULL'
            ))
        )
    return ids


def upgrade():
    bind = op.get_bind()
    linked = {column: _linked_ids(bind, column) for column, _ in LINKS}

    # Clear the links first so the foreign keys never point at deleted rows,
    # whether or not the database enforces ON DELETE SET NULL.
    for table_name in LINK_TABLES:
        columns = _columns(table_name)
        for column, _ in LINKS:
            if column in columns:
                bind.execute(sa.text(f'UPDATE {table_name} SET {column} = NULL WHERE {column} IS NOT NULL'))

    expense_ids = linked['expense_id']
    if expense_ids and 'generated_from_id' in _columns('expenses'):
        for chunk in _chunks(expense_ids):
            bind.execute(
                sa.text('UPDATE expenses SET generated_from_id = NULL WHERE generated_from_id IN :ids')
                .bindparams(sa.bindparam('ids', expanding=True)),
                {'ids': chunk},
            )

    for column, target_table in LINKS:
        for chunk in _chunks(linked[column]):
            bind.execute(
                sa.text(f'DELETE FROM {target_table} WHERE id IN :ids')
                .bindparams(sa.bindparam('ids', expanding=True)),
                {'ids': chunk},
            )


def downgrade():
    # Deleted incomes and expenses cannot be rebuilt reliably; see the docstring.
    pass
