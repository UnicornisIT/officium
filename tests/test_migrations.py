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

    def test_migration_graph_is_single_chain(self):
        revisions = {module.revision: module.down_revision for module in self.modules}

        self.assertEqual(revisions['73459c8513a1'], None)
        self.assertEqual(revisions['20260502_mortgage'], '73459c8513a1')
        self.assertEqual(revisions['20260502_log_ip_ua'], '20260502_mortgage')
        self.assertEqual(revisions['20260503_debt_type'], '20260502_log_ip_ua')

        referenced = {down for down in revisions.values() if down}
        heads = set(revisions) - referenced
        self.assertEqual(heads, {'20260503_debt_type'})

    def test_migrations_do_not_drop_tables(self):
        migration_text = '\n'.join(path.read_text(encoding='utf-8') for path in MIGRATIONS_DIR.glob('*.py'))

        self.assertNotIn('op.drop_table', migration_text)
        self.assertNotIn('db.drop_all', migration_text)

    def test_deploy_preflight_handles_migration_states(self):
        deploy_text = (PROJECT_ROOT / 'deploy.sh').read_text(encoding='utf-8')

        self.assertIn('stamp_baseline', deploy_text)
        self.assertIn('ADD COLUMN version_num', deploy_text)
        self.assertNotIn('stamp head', deploy_text)
