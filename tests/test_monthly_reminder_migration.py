import importlib.util
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from app import create_app
from extensions import db


ROOT = Path(__file__).resolve().parents[1]


class MonthlyReminderMigrationTests(unittest.TestCase):
    def test_existing_user_upgrade_downgrade_and_schema_check(self):
        with tempfile.TemporaryDirectory() as directory, patch('logging.config.fileConfig'):
            app = create_app(dict(TESTING=True, SECRET_KEY='migration-test',
                                  SQLALCHEMY_DATABASE_URI='sqlite:///' + (Path(directory) / 'app.sqlite').as_posix(),
                                  SQLALCHEMY_ENGINE_OPTIONS={}, TELEGRAM_BOT_ENABLED=False))
            runner = app.test_cli_runner()
            result = runner.invoke(args=['db', 'upgrade', '20261123_update_notifications'])
            self.assertEqual(result.exit_code, 0, result.output)
            with app.app_context(), db.engine.begin() as connection:
                connection.execute(text("INSERT INTO users (telegram_id, created_at, role, is_blocked, login_count) "
                                        "VALUES (12345, CURRENT_TIMESTAMP, 'user', 0, 0)"))
                foreign_key = next(row for row in inspect(connection).get_foreign_keys('expenses')
                                   if row['constrained_columns'] == ['generated_from_id'])
                operations = Operations(MigrationContext.configure(connection))
                convention = {'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s'}
                with operations.batch_alter_table('expenses', naming_convention=convention) as batch:
                    batch.drop_constraint(foreign_key['name']
                                          or 'fk_expenses_generated_from_id_expenses', type_='foreignkey')
                    batch.create_foreign_key('legacy_generated_from', 'expenses', ['generated_from_id'], ['id'])
                connection.execute(text("INSERT INTO expenses (user_id, amount, category, title, expense_date, is_monthly) "
                                        "VALUES ((SELECT id FROM users WHERE telegram_id=12345), 123.45, 'rent', "
                                        "'Migration fixture', '2026-01-31', 1)"))
            for args in (['db', 'upgrade'], ['db', 'check'], ['db', 'heads'],
                         ['db', 'downgrade', '20261123_update_notifications'],
                         ['db', 'upgrade'], ['db', 'check'], ['send-monthly-expense-reminders', '--dry-run']):
                result = runner.invoke(args=args)
                self.assertEqual(result.exit_code, 0, result.output)
            with app.app_context(), db.engine.connect() as connection:
                row = connection.execute(text('SELECT reminder_enabled, reminder_time, reminder_days_before, '
                                              'timezone FROM users WHERE telegram_id=12345')).one()
                self.assertEqual(tuple(row), (1, '10:00', 1, 'Europe/Moscow'))
                self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM expenses')).scalar(), 1)
                foreign_key = next(row for row in inspect(connection).get_foreign_keys('expenses')
                                   if row['constrained_columns'] == ['generated_from_id'])
                self.assertEqual(foreign_key['options'].get('ondelete'), 'SET NULL')
            with app.app_context():
                db.engine.dispose()

    def test_new_migration_compiles_for_mysql(self):
        spec = importlib.util.spec_from_file_location(
            'monthly_migration', ROOT / 'migrations/versions/20261124_monthly_reminders.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        for direction in ('upgrade', 'downgrade'):
            output = StringIO()
            context = MigrationContext.configure(dialect_name='mysql', opts={'as_sql': True, 'output_buffer': output})
            with Operations.context(context):
                getattr(migration, direction)()
            sql = output.getvalue()
            self.assertIn('monthly_expense_reminder_logs', sql)
            self.assertIn('ALTER TABLE users', sql)
