import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from tests.test_debt_accrual_migration import VERSIONS, load_migration, mysql_sql


class OpeningPrincipalMigrationTests(unittest.TestCase):
    def test_upgrade_and_rollback_preserve_legacy_amounts_and_history(self):
        module = load_migration(VERSIONS / '20261201_debt_opening_principal.py')
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE debts (id INTEGER PRIMARY KEY, remaining_amount NUMERIC(12,2))'))
            connection.execute(
                text('CREATE TABLE payments (id INTEGER PRIMARY KEY, debt_id INTEGER, amount NUMERIC(12,2))')
            )
            connection.execute(text('INSERT INTO debts VALUES (1, 450000)'))
            connection.execute(text('INSERT INTO payments VALUES (1, 1, 2539.73), (2, 1, 25000)'))
            before = connection.execute(text('SELECT * FROM payments ORDER BY id')).all()
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            self.assertEqual(
                connection.execute(text('SELECT opening_principal_amount, payment_day FROM debts')).one(), (None, None)
            )
            self.assertEqual(connection.execute(text('SELECT * FROM payments ORDER BY id')).all(), before)
            self.assertEqual(connection.execute(text('SELECT remaining_amount FROM debts')).scalar_one(), 450000)
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            self.assertEqual(
                {column['name'] for column in inspect(connection).get_columns('debts')}, {'id', 'remaining_amount'}
            )
            self.assertEqual(connection.execute(text('SELECT * FROM payments ORDER BY id')).all(), before)

    def test_mysql_migration_is_additive_without_backfill(self):
        module = load_migration(VERSIONS / '20261201_debt_opening_principal.py')
        upgrade = mysql_sql(module, 'upgrade')
        self.assertIn('ADD COLUMN opening_principal_amount NUMERIC(12, 2)', upgrade)
        self.assertIn('ADD COLUMN payment_day SMALLINT', upgrade)
        self.assertNotIn('UPDATE', upgrade)
        self.assertNotIn('payments', upgrade)
        self.assertIn('DROP COLUMN opening_principal_amount', mysql_sql(module, 'downgrade'))
