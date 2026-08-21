"""Link goal operations to expense and income cashflow entries.

Revision ID: 20260821_goalflow
Revises: 20260821_goals
Create Date: 2026-08-21 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260821_goalflow'
down_revision = '20260821_goals'
branch_labels = None
depends_on = None


MYSQL_EXPENSE_CATEGORY_SQL = (
    "ALTER TABLE expenses MODIFY category ENUM("
    "'products', 'transport', 'communication', 'rent', 'loans', "
    "'restaurants', 'entertainment', 'health', 'education', 'clothing', "
    "'subscriptions', 'savings', 'other') NOT NULL"
)
MYSQL_INCOME_CATEGORY_SQL = (
    "ALTER TABLE incomes MODIFY category ENUM("
    "'salary', 'advance', 'side_job', 'debt_return', 'bonus', "
    "'scholarship', 'vacation_pay', 'goal_withdrawal', 'other') NOT NULL"
)


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name):
    return table_name in _inspector().get_table_names()


def _column_exists(table_name, column_name):
    return column_name in {column['name'] for column in _inspector().get_columns(table_name)}


def _add_cashflow_links(table_name):
    if not _table_exists(table_name) or _column_exists(table_name, 'expense_id'):
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(sa.Column('expense_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('income_id', sa.Integer(), nullable=True))
        batch_op.create_unique_constraint(f'uq_{table_name}_expense_id', ['expense_id'])
        batch_op.create_unique_constraint(f'uq_{table_name}_income_id', ['income_id'])
        batch_op.create_foreign_key(
            f'fk_{table_name}_expense_id',
            'expenses',
            ['expense_id'],
            ['id'],
            ondelete='SET NULL',
        )
        batch_op.create_foreign_key(
            f'fk_{table_name}_income_id',
            'incomes',
            ['income_id'],
            ['id'],
            ondelete='SET NULL',
        )


def _create_cashflow_entry(bind, user_id, goal_name, transaction_type, amount, transaction_date, comment):
    automatic_note = f'Создано автоматически из операции цели «{goal_name}».'
    full_comment = f'{automatic_note} {comment}'.strip() if comment else automatic_note
    if transaction_type == 'deposit':
        result = bind.execute(sa.text("""
            INSERT INTO expenses
                (user_id, amount, category, title, expense_date, payment_method,
                 comment, created_at, is_monthly)
            VALUES
                (:user_id, :amount, 'savings', :title, :transaction_date, 'transfer',
                 :comment, CURRENT_TIMESTAMP, 0)
        """), {
            'user_id': user_id,
            'amount': amount,
            'title': f'Пополнение цели: {goal_name}'[:150],
            'transaction_date': transaction_date,
            'comment': full_comment,
        })
        return result.lastrowid, None

    result = bind.execute(sa.text("""
        INSERT INTO incomes
            (user_id, amount, category, source, income_date, comment, created_at)
        VALUES
            (:user_id, :amount, 'goal_withdrawal', :source, :transaction_date,
             :comment, CURRENT_TIMESTAMP)
    """), {
        'user_id': user_id,
        'amount': amount,
        'source': f'Снятие с цели: {goal_name}'[:150],
        'transaction_date': transaction_date,
        'comment': full_comment,
    })
    return None, result.lastrowid


def _backfill_transactions():
    bind = op.get_bind()
    emergency_rows = bind.execute(sa.text("""
        SELECT id, user_id, transaction_type, amount, transaction_date, comment
        FROM emergency_fund_transactions
        WHERE expense_id IS NULL AND income_id IS NULL
        ORDER BY id
    """)).mappings().all()
    for row in emergency_rows:
        expense_id, income_id = _create_cashflow_entry(
            bind,
            row['user_id'],
            'Финансовая подушка',
            row['transaction_type'],
            row['amount'],
            row['transaction_date'],
            row['comment'],
        )
        bind.execute(sa.text("""
            UPDATE emergency_fund_transactions
            SET expense_id = :expense_id, income_id = :income_id
            WHERE id = :transaction_id
        """), {'expense_id': expense_id, 'income_id': income_id, 'transaction_id': row['id']})

    custom_rows = bind.execute(sa.text("""
        SELECT tx.id, goal.user_id, goal.name, tx.transaction_type,
               tx.amount, tx.transaction_date, tx.comment
        FROM financial_goal_transactions AS tx
        JOIN financial_goals AS goal ON goal.id = tx.goal_id
        WHERE tx.expense_id IS NULL AND tx.income_id IS NULL
        ORDER BY tx.id
    """)).mappings().all()
    for row in custom_rows:
        expense_id, income_id = _create_cashflow_entry(
            bind,
            row['user_id'],
            row['name'],
            row['transaction_type'],
            row['amount'],
            row['transaction_date'],
            row['comment'],
        )
        bind.execute(sa.text("""
            UPDATE financial_goal_transactions
            SET expense_id = :expense_id, income_id = :income_id
            WHERE id = :transaction_id
        """), {'expense_id': expense_id, 'income_id': income_id, 'transaction_id': row['id']})


def upgrade():
    if op.get_bind().dialect.name in ('mysql', 'mariadb'):
        if _table_exists('expenses'):
            op.execute(MYSQL_EXPENSE_CATEGORY_SQL)
        if _table_exists('incomes'):
            op.execute(MYSQL_INCOME_CATEGORY_SQL)

    _add_cashflow_links('emergency_fund_transactions')
    _add_cashflow_links('financial_goal_transactions')
    if _table_exists('emergency_fund_transactions') and _table_exists('financial_goal_transactions'):
        _backfill_transactions()


def downgrade():
    pass
