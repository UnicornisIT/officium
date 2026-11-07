import importlib.util
import inspect as python_inspect
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.migration_preflight import (
    BASELINE_REVISION,
    BASELINE_TABLES,
    MigrationPreflightError,
    inspect_migration_state,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / 'migrations' / 'versions'


def load_migration_modules():
    modules = []
    for path in sorted(MIGRATIONS_DIR.glob('*.py')):
        module_name = f'migration_{path.stem}'
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


class MigrationContractTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = load_migration_modules()

    def test_revision_ids_fit_mysql_alembic_version_column(self):
        for module in self.modules:
            self.assertLess(len(module.revision), 32, module.revision)

    def test_migration_graph_has_single_head(self):
        revisions = {module.revision: module.down_revision for module in self.modules}

        self.assertEqual(revisions['73459c8513a1'], None)
        self.assertEqual(revisions['20260502_mortgage'], '73459c8513a1')
        self.assertEqual(revisions['20260502_log_ip_ua'], '20260502_mortgage')
        self.assertEqual(revisions['20260503_debt_type'], '20260502_log_ip_ua')
        self.assertEqual(revisions['acd5bddc3168'], '20260503_debt_type')
        self.assertEqual(revisions['e49a6c3dc4b8'], 'acd5bddc3168')
        self.assertEqual(revisions['20260517_monthly_expenses'], '20260503_debt_type')
        self.assertEqual(
            set(revisions['20260517_merge_heads']),
            {'e49a6c3dc4b8', '20260517_monthly_expenses'},
        )
        self.assertEqual(revisions['20260729_restaurants'], '20260517_merge_heads')
        self.assertEqual(revisions['20260729_vacpay'], '20260729_restaurants')
        self.assertEqual(revisions['20260729_conscred'], '20260729_vacpay')
        self.assertEqual(revisions['20260729_debtrecur'], '20260729_conscred')
        self.assertEqual(revisions['20260729_debtrate'], '20260729_debtrecur')
        self.assertEqual(revisions['20260729_earlypay'], '20260729_debtrate')
        self.assertEqual(revisions['20260729_paybreak'], '20260729_earlypay')
        self.assertEqual(revisions['20260729_bankcalc'], '20260729_paybreak')
        self.assertEqual(revisions['20260729_splitbuy'], '20260729_bankcalc')
        self.assertEqual(revisions['20260729_tgupdates'], '20260729_splitbuy')
        self.assertEqual(revisions['20260729_tgstate'], '20260729_tgupdates')
        self.assertEqual(revisions['20260821_finplan'], '20260729_tgstate')
        self.assertEqual(revisions['20260821_fundtx'], '20260821_finplan')
        self.assertEqual(revisions['20260821_goals'], '20260821_fundtx')
        self.assertEqual(revisions['20260821_goalflow'], '20260821_goals')
        self.assertEqual(revisions['20260823_earlyplan'], '20260821_goalflow')
        self.assertEqual(revisions['20260823_firstpay'], '20260823_earlyplan')
        self.assertEqual(revisions['20260824_combpay'], '20260823_firstpay')
        self.assertEqual(revisions['20260829_integrity'], '20260824_combpay')
        self.assertEqual(revisions['20261009_monthly_settings'], '20260829_integrity')
        self.assertEqual(revisions['20261012_audit_log'], '20261009_monthly_settings')
        self.assertEqual(revisions['20261012_impersonation'], '20261012_audit_log')
        self.assertEqual(revisions['20261012_retire_settings'], '20261012_impersonation')
        self.assertEqual(revisions['20261012_dictionary_types'], '20261012_retire_settings')
        self.assertEqual(revisions['20261013_debt_enums'], '20261012_dictionary_types')
        self.assertEqual(revisions['20261014_debt_accrual'], '20261013_debt_enums')
        self.assertEqual(revisions['20261015_accrual_opening'], '20261014_debt_accrual')
        self.assertEqual(revisions['20261016_goal_transfer_data'], '20261015_accrual_opening')
        self.assertEqual(revisions['20261016_drop_goal_cashflow'], '20261016_goal_transfer_data')
        self.assertEqual(revisions['20261017_pay_period_settings'], '20261016_drop_goal_cashflow')
        self.assertEqual(revisions['20261018_last_seen_release'], '20261017_pay_period_settings')

        referenced = set()
        for down_revision in revisions.values():
            if isinstance(down_revision, tuple):
                referenced.update(down_revision)
            elif down_revision:
                referenced.add(down_revision)
        heads = set(revisions) - referenced
        self.assertEqual(revisions['20261027_vehicle_core'], '20261018_last_seen_release')
        self.assertEqual(revisions['20261028_vehicle_costs'], '20261027_vehicle_core')
        self.assertEqual(revisions['20261028_vehicle_cost_details'], '20261028_vehicle_costs')
        self.assertEqual(revisions['20261029_vehicle_maintenance'], '20261028_vehicle_cost_details')
        self.assertEqual(revisions['20261029_vehicle_fuel'], '20261029_vehicle_maintenance')
        self.assertEqual(revisions['20261105_vehicle_documents'], '20261029_vehicle_fuel')
        self.assertEqual(revisions['20261106_vehicle_notifications'], '20261105_vehicle_documents')
        self.assertEqual(revisions['20261120_vehicle_attachments'], '20261106_vehicle_notifications')
        self.assertEqual(revisions['20261121_vehicle_identifiers'], '20261120_vehicle_attachments')
        self.assertEqual(revisions['20261122_personal_settings'], '20261121_vehicle_identifiers')
        self.assertEqual(revisions['20261123_update_notifications'], '20261122_personal_settings')
        self.assertEqual(revisions['20261124_monthly_reminders'], '20261123_update_notifications')
        self.assertEqual(revisions['20261125_feedback'], '20261124_monthly_reminders')
        self.assertEqual(revisions['20261126_attachment_dedup'], '20261125_feedback')
        self.assertEqual(revisions['20261127_debt_payment_plans'], '20261126_attachment_dedup')
        self.assertEqual(revisions['20261128_payment_cascade'], '20261127_debt_payment_plans')
        self.assertEqual(revisions['20261129_monthly_expense_stops'], '20261128_payment_cascade')
        self.assertEqual(revisions['20261130_expense_auto'], '20261129_monthly_expense_stops')
        self.assertEqual(revisions['20261201_debt_opening'], '20261130_expense_auto')
        self.assertEqual(heads, {'20261201_debt_opening'})

    def test_payment_cascade_migration_preserves_data_and_deletes_payments(self):
        migration = next(module for module in self.modules if module.revision == '20261128_payment_cascade')
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            connection.exec_driver_sql('CREATE TABLE debts (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql(
                'CREATE TABLE payments (id INTEGER PRIMARY KEY, debt_id INTEGER NOT NULL, '
                'amount NUMERIC(12, 2) NOT NULL, FOREIGN KEY(debt_id) REFERENCES debts(id))'
            )
            connection.exec_driver_sql('CREATE INDEX ix_payments_debt_date ON payments (debt_id)')
            connection.exec_driver_sql('INSERT INTO debts VALUES (1), (2)')
            connection.exec_driver_sql('INSERT INTO payments VALUES (1, 1, 7302), (2, 2, 25000)')
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, 'op', operations):
                migration.upgrade()
                self.assertEqual(connection.execute(text('SELECT amount FROM payments ORDER BY id')).scalars().all(), [7302, 25000])
                self.assertEqual(inspect(connection).get_foreign_keys('payments')[0]['options']['ondelete'], 'CASCADE')
                self.assertEqual(inspect(connection).get_indexes('payments')[0]['name'], 'ix_payments_debt_date')
                connection.execute(text('DELETE FROM debts WHERE id = 1'))
                self.assertEqual(connection.execute(text('SELECT id FROM payments')).scalars().all(), [2])
                migration.downgrade()
                self.assertNotEqual(inspect(connection).get_foreign_keys('payments')[0]['options'].get('ondelete'), 'CASCADE')
                self.assertEqual(connection.execute(text('SELECT amount FROM payments')).scalar(), 25000)
        engine.dispose()

    def test_payment_cascade_migration_emits_mysql_and_mariadb_ddl(self):
        migration = next(module for module in self.modules if module.revision == '20261128_payment_cascade')
        for dialect in ('mysql', 'mariadb'):
            with self.subTest(dialect=dialect):
                output = io.StringIO()
                context = MigrationContext.configure(
                    url=f'{dialect}+pymysql://', opts={'as_sql': True, 'output_buffer': output},
                )
                with patch.object(migration.sa, 'inspect') as inspector:
                    inspector.return_value.get_foreign_keys.return_value = [{
                        'name': 'payments_ibfk_1', 'constrained_columns': ['debt_id'],
                        'referred_table': 'debts',
                    }]
                    with patch.object(migration, 'op', Operations(context)):
                        migration.upgrade()
                sql = output.getvalue()
                self.assertIn('ALTER TABLE payments DROP FOREIGN KEY payments_ibfk_1', sql)
                self.assertIn('FOREIGN KEY(debt_id) REFERENCES debts (id) ON DELETE CASCADE', sql)

    def test_monthly_expense_stop_migration_emits_mysql_and_mariadb_ddl(self):
        migration = next(module for module in self.modules if module.revision == '20261129_monthly_expense_stops')
        for dialect in ('mysql', 'mariadb'):
            with self.subTest(dialect=dialect):
                output = io.StringIO()
                context = MigrationContext.configure(
                    url=f'{dialect}+pymysql://', opts={'as_sql': True, 'output_buffer': output},
                )
                with patch.object(migration, 'op', Operations(context)):
                    migration.upgrade()
                    migration.downgrade()
                sql = output.getvalue()
                self.assertIn('CREATE TABLE monthly_expense_stops', sql)
                self.assertIn('PRIMARY KEY (user_id, monthly_group_id)', sql)
                self.assertIn('ON DELETE CASCADE', sql)
                self.assertIn('DROP TABLE monthly_expense_stops', sql)

    def test_expense_generation_source_preserves_confirmed_legacy_records(self):
        migration = next(module for module in self.modules if module.revision == '20261130_expense_auto')
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.exec_driver_sql(
                'CREATE TABLE expenses (id INTEGER PRIMARY KEY, generated_from_id INTEGER, '
                'is_monthly BOOLEAN, monthly_settings_updated_at DATETIME, created_at DATETIME)'
            )
            connection.execute(text(
                "INSERT INTO expenses VALUES "
                "(1, NULL, 1, '2026-04-01', '2026-04-01'), "
                "(2, 1, 1, '2026-04-01', '2026-05-01'), "
                "(3, 1, 1, '2026-06-02', '2026-06-01'), "
                "(4, 1, 1, NULL, '2026-07-01')"
            ))
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                self.assertEqual(connection.execute(text(
                    'SELECT monthly_is_automatic FROM expenses ORDER BY id'
                )).scalars().all(), [0, 1, 0, 0])
                migration.downgrade()
                self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM expenses')).scalar(), 4)
        engine.dispose()

    def test_migrations_do_not_drop_tables(self):
        migration_text = '\n'.join(path.read_text(encoding='utf-8') for path in MIGRATIONS_DIR.glob('*.py'))

        upgrades = '\n'.join(python_inspect.getsource(module.upgrade) for module in self.modules)
        self.assertNotIn('op.drop_table', upgrades)
        self.assertNotIn('db.drop_all', migration_text)

    def test_telegram_state_text_column_has_no_server_default(self):
        migration_text = (
            MIGRATIONS_DIR / '20260729_add_telegram_conversation_states.py'
        ).read_text(encoding='utf-8')

        self.assertIn("sa.Column('data', sa.Text(), nullable=False)", migration_text)
        self.assertNotIn("sa.Text(), nullable=False, server_default", migration_text)

    def test_deploy_preflight_handles_migration_states(self):
        deploy_text = (PROJECT_ROOT / 'deployment' / 'deploy-release').read_text(encoding='utf-8')
        preflight_text = (
            PROJECT_ROOT / 'app' / 'migration_preflight.py'
        ).read_text(encoding='utf-8')

        self.assertIn('-m flask db-preflight', deploy_text)
        self.assertIn('-m flask db-baseline-revision', deploy_text)
        self.assertIn('ALTER TABLE {VERSION_TABLE} ADD COLUMN version_num', preflight_text)
        self.assertNotIn('stamp head', deploy_text)
        self.assertNotIn("<<'PY'", deploy_text)

    def test_db_preflight_classifies_empty_baseline_and_ready_schemas(self):
        empty_engine = create_engine('sqlite:///:memory:')
        self.assertEqual(inspect_migration_state(empty_engine)[0], 'empty')

        baseline_engine = create_engine('sqlite:///:memory:')
        with baseline_engine.begin() as connection:
            for table_name in BASELINE_TABLES:
                connection.execute(text(f'CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)'))
        self.assertEqual(inspect_migration_state(baseline_engine)[0], 'stamp_baseline')

        ready_engine = create_engine('sqlite:///:memory:')
        with ready_engine.begin() as connection:
            connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
            connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32))'))
            connection.execute(
                text('INSERT INTO alembic_version (version_num) VALUES (:revision)'),
                {'revision': BASELINE_REVISION},
            )
        self.assertEqual(inspect_migration_state(ready_engine)[0], 'ready')

    def test_db_preflight_rejects_partial_unversioned_schema(self):
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))

        with self.assertRaises(MigrationPreflightError):
            inspect_migration_state(engine)

    def test_release_deploy_requires_backup_and_exact_tag(self):
        deploy_text = (PROJECT_ROOT / 'scripts' / 'deploy.sh').read_text(encoding='utf-8')

        self.assertIn('Deployment requires --release <tag>.', deploy_text)
        self.assertIn('set -euo pipefail', deploy_text)
        self.assertIn('officium-update-runner --start "$RELEASE_TAG"', deploy_text)
        self.assertNotIn('git pull', deploy_text)
        self.assertNotIn('git checkout', deploy_text)
        self.assertNotIn('sudo ', deploy_text)

    def test_monthly_expense_timer_is_persistent_and_has_daily_recovery(self):
        timer_text = (
            PROJECT_ROOT / 'deployment' / 'officium-monthly-expenses.timer'
        ).read_text(encoding='utf-8')
        service_text = (
            PROJECT_ROOT / 'deployment' / 'officium-monthly-expenses.service'
        ).read_text(encoding='utf-8')

        self.assertIn('OnCalendar=*-*-* 00:05:00 @@APP_TIMEZONE@@', timer_text)
        self.assertIn('Persistent=true', timer_text)
        self.assertIn('@@APP_ROOT@@/current', service_text)
        self.assertIn('EnvironmentFile=@@APP_ROOT@@/shared/.env', service_text)
        self.assertIn('generate-monthly-expenses', service_text)
        self.assertNotIn('gunicorn', service_text.lower())

    def test_windows_start_script_uses_local_safe_defaults(self):
        start_text = (PROJECT_ROOT / 'scripts' / 'start.bat').read_text(encoding='utf-8')

        self.assertIn('cd /d "%~dp0.."', start_text)
        self.assertIn('OFFICIUM_ENV=development', start_text)
        self.assertIn('DB_ENGINE=sqlite', start_text)
        self.assertIn('DEV_LOGIN_ENABLED=true', start_text)
        self.assertIn('FLASK_DEBUG=true', start_text)
        self.assertIn('HOST=127.0.0.1', start_text)
        self.assertIn('Text.UTF8Encoding($false)', start_text)
        self.assertNotIn('pip install --upgrade pip', start_text)
        self.assertIn('python -m flask --app run:app db upgrade', start_text)
        self.assertLess(start_text.index('python -m pip install -r requirements.txt'),
                        start_text.index('python -m flask --app run:app db upgrade'))
        self.assertLess(start_text.index('python -m flask --app run:app db upgrade'),
                        start_text.index('python run.py'))
        self.assertIn('if errorlevel 1', start_text.split('db upgrade', 1)[1].split('python run.py', 1)[0])
