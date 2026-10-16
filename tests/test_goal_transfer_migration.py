"""Migrations that turn goal operations into transfers, run on SQLite."""

import importlib.util
import io
from pathlib import Path
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


VERSIONS = Path(__file__).resolve().parents[1] / 'migrations' / 'versions'


def load_migration(name):
    path = VERSIONS / name
    spec = importlib.util.spec_from_file_location(f'migration_{path.stem}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCHEMA = (
    'CREATE TABLE expenses (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount NUMERIC(12, 2) NOT NULL, '
    'category VARCHAR(20) NOT NULL, title VARCHAR(150), generated_from_id INTEGER REFERENCES expenses (id))',
    'CREATE TABLE incomes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount NUMERIC(12, 2) NOT NULL, '
    'category VARCHAR(20) NOT NULL, source VARCHAR(150))',
    # One clause per line, as SQLAlchemy emits it: SQLite reflection of CHECK
    # constraints parses the table SQL line by line.
    'CREATE TABLE financial_goal_transactions (\n'
    '\tid INTEGER PRIMARY KEY, \n'
    '\tgoal_id INTEGER NOT NULL, \n'
    '\ttransaction_type VARCHAR(10) NOT NULL, \n'
    '\tamount NUMERIC(12, 2) NOT NULL, \n'
    '\texpense_id INTEGER, \n'
    '\tincome_id INTEGER, \n'
    '\tCONSTRAINT ck_financial_goal_transaction_amount_positive CHECK (amount > 0), \n'
    '\tCONSTRAINT uq_financial_goal_transactions_expense_id UNIQUE (expense_id), \n'
    '\tCONSTRAINT uq_financial_goal_transactions_income_id UNIQUE (income_id), \n'
    '\tCONSTRAINT fk_financial_goal_transactions_expense_id FOREIGN KEY(expense_id) REFERENCES expenses (id) ON DELETE SET NULL, \n'
    '\tCONSTRAINT fk_financial_goal_transactions_income_id FOREIGN KEY(income_id) REFERENCES incomes (id) ON DELETE SET NULL\n'
    ')',
    # Unnamed constraints, as db.create_all() produces them.
    'CREATE TABLE emergency_fund_transactions (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, '
    'transaction_type VARCHAR(10) NOT NULL, amount NUMERIC(12, 2) NOT NULL, '
    'expense_id INTEGER UNIQUE REFERENCES expenses (id) ON DELETE SET NULL, '
    'income_id INTEGER UNIQUE REFERENCES incomes (id) ON DELETE SET NULL)',
)


class GoalTransferMigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.data = load_migration('20261016_goal_transfer_data.py')
        self.schema = load_migration('20261016_drop_goal_cashflow.py')
        self.engine = create_engine('sqlite:///:memory:')
        with self.engine.begin() as connection:
            for statement in SCHEMA:
                connection.execute(text(statement))
            connection.execute(text(
                "INSERT INTO expenses (id, user_id, amount, category, title) VALUES "
                "(1, 1, 5000, 'savings', 'Пополнение цели: Отпуск'), "      # linked to a goal
                "(2, 1, 3000, 'savings', 'Пополнение цели: Финансовая подушка'), "  # linked to the fund
                "(3, 1, 7000, 'savings', 'Пополнение цели: Отпуск'), "      # manual, same text
                "(4, 1, 900, 'products', 'Продукты')"
            ))
            connection.execute(text(
                "INSERT INTO expenses (id, user_id, amount, category, title, generated_from_id) VALUES "
                "(5, 1, 900, 'products', 'Продукты', 1)"
            ))
            connection.execute(text(
                "INSERT INTO incomes (id, user_id, amount, category, source) VALUES "
                "(10, 1, 1000, 'goal_withdrawal', 'Снятие с цели: Отпуск'), "  # linked
                "(11, 1, 400, 'goal_withdrawal', 'Снятие с цели: Финансовая подушка'), "  # linked
                "(12, 1, 2500, 'goal_withdrawal', 'Снятие с цели: Отпуск'), "  # manual
                "(13, 1, 90000, 'salary', 'Работа')"
            ))
            connection.execute(text(
                "INSERT INTO financial_goal_transactions VALUES "
                "(1, 7, 'deposit', 5000, 1, NULL), (2, 7, 'withdrawal', 1000, NULL, 10), "
                "(3, 7, 'deposit', 200, NULL, NULL)"
            ))
            connection.execute(text(
                "INSERT INTO emergency_fund_transactions VALUES "
                "(1, 1, 'deposit', 3000, 2, NULL), (2, 1, 'withdrawal', 400, NULL, 11)"
            ))

    def run_step(self, module, step):
        with self.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                getattr(module, step)()

    def ids(self, table):
        with self.engine.connect() as connection:
            return {row[0] for row in connection.execute(text(f'SELECT id FROM {table}'))}

    def columns(self, table):
        return {column['name'] for column in inspect(self.engine).get_columns(table)}

    def test_deletes_only_linked_records_and_keeps_manual_ones(self):
        self.run_step(self.data, 'upgrade')

        self.assertEqual(self.ids('expenses'), {3, 4, 5})
        self.assertEqual(self.ids('incomes'), {12, 13})
        self.assertEqual(self.ids('financial_goal_transactions'), {1, 2, 3})
        self.assertEqual(self.ids('emergency_fund_transactions'), {1, 2})
        with self.engine.connect() as connection:
            for table in ('financial_goal_transactions', 'emergency_fund_transactions'):
                links = connection.execute(text(
                    f'SELECT COUNT(*) FROM {table} WHERE expense_id IS NOT NULL OR income_id IS NOT NULL'
                )).scalar()
                self.assertEqual(links, 0, table)
            generated_from = connection.execute(text('SELECT generated_from_id FROM expenses WHERE id = 5')).scalar()
        self.assertIsNone(generated_from)

    def test_data_migration_is_idempotent(self):
        self.run_step(self.data, 'upgrade')
        self.run_step(self.data, 'upgrade')

        self.assertEqual(self.ids('expenses'), {3, 4, 5})
        self.assertEqual(self.ids('incomes'), {12, 13})

    def test_schema_migration_drops_links_and_downgrade_restores_columns(self):
        self.run_step(self.data, 'upgrade')
        self.run_step(self.schema, 'upgrade')

        for table in ('financial_goal_transactions', 'emergency_fund_transactions'):
            self.assertFalse({'expense_id', 'income_id'} & self.columns(table), table)
        # The rebuilt table keeps its rows and its CHECK constraint.
        self.assertEqual(self.ids('financial_goal_transactions'), {1, 2, 3})
        with self.assertRaises(Exception):
            with self.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO financial_goal_transactions (id, goal_id, transaction_type, amount) "
                    "VALUES (9, 7, 'deposit', 0)"
                ))

        self.run_step(self.schema, 'downgrade')
        self.run_step(self.data, 'downgrade')

        for table in ('financial_goal_transactions', 'emergency_fund_transactions'):
            self.assertTrue({'expense_id', 'income_id'} <= self.columns(table), table)
            foreign_keys = {tuple(item['constrained_columns']) for item in inspect(self.engine).get_foreign_keys(table)}
            self.assertTrue({('expense_id',), ('income_id',)} <= foreign_keys, table)

    def test_mysql_schema_upgrade_drops_foreign_keys_before_columns(self):
        buffer = io.StringIO()
        context = MigrationContext.configure(dialect_name='mysql', opts={'as_sql': True, 'output_buffer': buffer})

        class FakeInspector:
            def get_table_names(self):
                return ['financial_goal_transactions']

            def get_columns(self, table_name):
                return [{'name': name} for name in ('id', 'expense_id', 'income_id')]

            def get_foreign_keys(self, table_name):
                return [
                    {'name': 'financial_goal_transactions_ibfk_2', 'constrained_columns': ['expense_id']},
                    {'name': 'financial_goal_transactions_ibfk_3', 'constrained_columns': ['income_id']},
                ]

            def get_unique_constraints(self, table_name):
                return [{'name': 'expense_id', 'column_names': ['expense_id']}]

            def get_indexes(self, table_name):
                return [
                    {'name': 'expense_id', 'column_names': ['expense_id']},
                    {'name': 'income_id', 'column_names': ['income_id']},
                ]

        self.schema._inspector = FakeInspector
        with Operations.context(context):
            self.schema.upgrade()
        sql = buffer.getvalue()

        drop_fk = sql.index('DROP FOREIGN KEY financial_goal_transactions_ibfk_2')
        drop_index = sql.index('DROP INDEX expense_id')
        drop_column = sql.index('DROP COLUMN expense_id')
        self.assertLess(drop_fk, drop_index)
        self.assertLess(drop_index, drop_column)
        self.assertIn('DROP COLUMN income_id', sql)
        self.assertEqual(sql.count('DROP INDEX expense_id'), 1)


if __name__ == '__main__':
    unittest.main()
