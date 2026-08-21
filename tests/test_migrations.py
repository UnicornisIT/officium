import importlib.util
from pathlib import Path
import unittest


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

        referenced = set()
        for down_revision in revisions.values():
            if isinstance(down_revision, tuple):
                referenced.update(down_revision)
            elif down_revision:
                referenced.add(down_revision)
        heads = set(revisions) - referenced
        self.assertEqual(heads, {'20260821_goalflow'})

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
        deploy_text = (PROJECT_ROOT / 'deploy.sh').read_text(encoding='utf-8')

        self.assertIn('stamp_baseline', deploy_text)
        self.assertIn('ADD COLUMN version_num', deploy_text)
        self.assertNotIn('stamp head', deploy_text)
