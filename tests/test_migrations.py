import importlib.util
from pathlib import Path
import unittest

from sqlalchemy import create_engine, text

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

        referenced = set()
        for down_revision in revisions.values():
            if isinstance(down_revision, tuple):
                referenced.update(down_revision)
            elif down_revision:
                referenced.add(down_revision)
        heads = set(revisions) - referenced
        self.assertEqual(heads, {'20261009_monthly_settings'})

    def test_migrations_do_not_drop_tables(self):
        migration_text = '\n'.join(path.read_text(encoding='utf-8') for path in MIGRATIONS_DIR.glob('*.py'))

        self.assertNotIn('op.drop_table', migration_text)
        self.assertNotIn('db.drop_all', migration_text)

    def test_telegram_state_text_column_has_no_server_default(self):
        migration_text = (
            MIGRATIONS_DIR / '20260729_add_telegram_conversation_states.py'
        ).read_text(encoding='utf-8')

        self.assertIn("sa.Column('data', sa.Text(), nullable=False)", migration_text)
        self.assertNotIn("sa.Text(), nullable=False, server_default", migration_text)

    def test_deploy_preflight_handles_migration_states(self):
        deploy_text = (PROJECT_ROOT / 'scripts' / 'deploy.sh').read_text(encoding='utf-8')
        preflight_text = (
            PROJECT_ROOT / 'app' / 'migration_preflight.py'
        ).read_text(encoding='utf-8')

        self.assertIn('flask db-preflight', deploy_text)
        self.assertIn('flask db-baseline-revision', deploy_text)
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

        self.assertIn('OFFICIUM_BACKUP_CONFIRMED', deploy_text)
        self.assertIn('refs/tags/$RELEASE_TAG:refs/tags/$RELEASE_TAG', deploy_text)
        self.assertIn('git checkout --detach', deploy_text)
        self.assertIn('Tracked local changes found', deploy_text)
        self.assertIn('Deployment requires --release <tag>.', deploy_text)
        self.assertIn('set -euo pipefail', deploy_text)
        self.assertNotIn('git pull', deploy_text)
        self.assertNotIn('python3 -m venv', deploy_text)

    def test_monthly_expense_timer_is_persistent_and_has_daily_recovery(self):
        timer_text = (
            PROJECT_ROOT / 'deployment' / 'officium-monthly-expenses.timer'
        ).read_text(encoding='utf-8')
        service_text = (
            PROJECT_ROOT / 'deployment' / 'officium-monthly-expenses.service'
        ).read_text(encoding='utf-8')

        self.assertIn('OnCalendar=*-*-* 00:05:00 Europe/Moscow', timer_text)
        self.assertIn('Persistent=true', timer_text)
        self.assertIn('generate-monthly-expenses', service_text)
        self.assertNotIn('gunicorn', service_text.lower())

    def test_windows_start_script_uses_local_safe_defaults(self):
        start_text = (PROJECT_ROOT / 'scripts' / 'start.bat').read_text(encoding='utf-8')

        self.assertIn('cd /d "%~dp0.."', start_text)
        self.assertIn('OFFICIUM_ENV=development', start_text)
        self.assertIn('DB_ENGINE=sqlite', start_text)
        self.assertIn('DEV_LOGIN_ENABLED=true', start_text)
        self.assertNotIn('pip install --upgrade pip', start_text)
