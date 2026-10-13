import importlib.util
import io
from datetime import date
from pathlib import Path
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


VERSIONS = Path(__file__).resolve().parents[1] / 'migrations' / 'versions'
MIGRATION_PATH = VERSIONS / '20261014_debt_accrual.py'


def load_migration(path=MIGRATION_PATH):
    spec = importlib.util.spec_from_file_location(f'migration_{path.stem}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mysql_sql(module, step):
    """Offline (as_sql) MySQL DDL of a migration step, without a server."""
    buffer = io.StringIO()
    context = MigrationContext.configure(dialect_name='mysql', opts={'as_sql': True, 'output_buffer': buffer})
    module._column_exists = lambda table_name, column_name: step == 'downgrade'
    if hasattr(module, '_backfill'):
        module._backfill = lambda: None  # needs a live connection
    with Operations.context(context):
        getattr(module, step)()
    return buffer.getvalue()


class DebtAccrualMigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.migration = load_migration()
        self.engine = create_engine('sqlite:///:memory:')
        with self.engine.begin() as connection:
            connection.execute(text(
                'CREATE TABLE debts (id INTEGER PRIMARY KEY, debt_type VARCHAR(20) NOT NULL, '
                'next_payment_date DATE, interest_period_start_date DATE)'
            ))
            connection.execute(text(
                'CREATE TABLE payments (id INTEGER PRIMARY KEY, debt_id INTEGER NOT NULL, '
                'interest_amount NUMERIC(12, 2) NOT NULL, fee_amount NUMERIC(12, 2) NOT NULL)'
            ))
            connection.execute(text(
                "INSERT INTO debts VALUES "
                "(1, 'consumer_credit', '2026-10-21', NULL), "
                "(2, 'split', '2026-08-12', NULL), "
                "(3, 'mortgage', '2026-10-15', '2026-10-01'), "
                "(4, 'credit_card', NULL, NULL), "
                "(5, 'consumer_credit', '2026-03-31', NULL)"
            ))
            connection.execute(text(
                'INSERT INTO payments VALUES '
                '(1, 1, 0, 0), (2, 1, 171.93, 0), (3, 1, 0, 49.00)'
            ))

    def run_migration(self, step):
        with self.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                getattr(self.migration, step)()

    def columns(self, table_name):
        return {column['name'] for column in inspect(self.engine).get_columns(table_name)}

    def test_upgrade_adds_columns_and_backfills_existing_rows(self):
        self.run_migration('upgrade')

        self.assertTrue({
            'penalty_rate', 'capitalize_interest', 'grace_period_end_date', 'accrual_start_date',
        } <= self.columns('debts'))
        self.assertTrue({'penalty_amount', 'breakdown_source'} <= self.columns('payments'))

        with self.engine.connect() as connection:
            debts = {
                row.id: row
                for row in connection.execute(text(
                    'SELECT id, penalty_rate, capitalize_interest, accrual_start_date FROM debts'
                ))
            }
            payments = dict(connection.execute(text(
                'SELECT id, breakdown_source FROM payments'
            )).fetchall())

        # Existing debts keep an empty penalty rate and no capitalization.
        self.assertTrue(all(row.penalty_rate is None for row in debts.values()))
        self.assertTrue(all(not row.capitalize_interest for row in debts.values()))
        # Monthly cycle: 21.10 -> 21.09; split cycle on the 12th/27th: 12.08 -> 27.07;
        # a later interest period start wins; 31.03 -> 28.02 (end-of-month clamp).
        self.assertEqual(str(debts[1].accrual_start_date), str(date(2026, 9, 21)))
        self.assertEqual(str(debts[2].accrual_start_date), str(date(2026, 7, 27)))
        self.assertEqual(str(debts[3].accrual_start_date), str(date(2026, 10, 1)))
        self.assertIsNone(debts[4].accrual_start_date)
        self.assertEqual(str(debts[5].accrual_start_date), str(date(2026, 2, 28)))
        self.assertEqual(payments, {1: 'auto', 2: 'manual', 3: 'manual'})

    def test_downgrade_removes_added_columns(self):
        self.run_migration('upgrade')
        self.run_migration('downgrade')

        self.assertEqual(
            self.columns('debts'),
            {'id', 'debt_type', 'next_payment_date', 'interest_period_start_date'},
        )
        self.assertEqual(self.columns('payments'), {'id', 'debt_id', 'interest_amount', 'fee_amount'})

    def test_upgrade_is_idempotent(self):
        self.run_migration('upgrade')
        self.run_migration('upgrade')

        self.assertIn('accrual_start_date', self.columns('debts'))



class DebtAccrualMigrationMySqlTestCase(unittest.TestCase):
    def test_accrual_columns_compile_for_mysql(self):
        sql = mysql_sql(load_migration(), 'upgrade')

        self.assertIn('ALTER TABLE debts ADD COLUMN penalty_rate NUMERIC(5, 2)', sql)
        self.assertIn('ALTER TABLE debts ADD COLUMN capitalize_interest BOOL NOT NULL DEFAULT false', sql)
        self.assertIn("ALTER TABLE payments ADD COLUMN breakdown_source ENUM('auto','manual') NOT NULL DEFAULT 'auto'", sql)
        self.assertIn('ALTER TABLE payments ALTER COLUMN breakdown_source DROP DEFAULT', sql)
        downgrade = mysql_sql(load_migration(), 'downgrade')
        self.assertIn('ALTER TABLE payments DROP COLUMN breakdown_source', downgrade)
        self.assertIn('ALTER TABLE debts DROP COLUMN accrual_start_date', downgrade)

    def test_opening_balance_columns_compile_for_mysql(self):
        module = load_migration(VERSIONS / '20261015_accrual_opening_balance.py')

        sql = mysql_sql(module, 'upgrade')

        self.assertIn("ALTER TABLE debts ADD COLUMN opening_interest_amount NUMERIC(12, 2) NOT NULL DEFAULT '0'", sql)
        self.assertIn('ALTER TABLE debts ALTER COLUMN opening_penalty_amount DROP DEFAULT', sql)
        self.assertIn('ALTER TABLE debts DROP COLUMN opening_interest_amount', mysql_sql(module, 'downgrade'))

if __name__ == '__main__':
    unittest.main()
