import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from alembic.config import Config
from flask_migrate import upgrade, check
from sqlalchemy import inspect, text
from app import create_app
from extensions import db

ROOT = Path(__file__).resolve().parents[1]


class MaintenanceMigrationTests(unittest.TestCase):
    def test_upgrade_seeds_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch('logging.config.fileConfig'):
            app = create_app(dict(TESTING=True, SECRET_KEY='migration', SQLALCHEMY_DATABASE_URI='sqlite:///'+(Path(directory)/'test.sqlite').as_posix(), SQLALCHEMY_ENGINE_OPTIONS={}))
            with app.app_context():
                try:
                    upgrade(directory=str(ROOT/'migrations'), revision='20261028_vehicle_cost_details')
                    upgrade(directory=str(ROOT/'migrations'))
                    self.assertIn('vehicle_service_items', inspect(db.engine).get_table_names())
                    with db.engine.connect() as conn:
                        self.assertEqual(conn.execute(text('SELECT COUNT(*) FROM consumable_templates')).scalar(), 29)
                        for group, km in [('passenger', 10000), ('truck', 30000)]:
                            row = conn.execute(text('SELECT interval_km, interval_months, applies_to_electric FROM consumable_templates WHERE template_set=:group AND sort_order=0'), dict(group=group)).one()
                            self.assertEqual(tuple(row), (km, 12, 0))
                    check(directory=str(ROOT/'migrations'))
                    config = Config()
                    config.set_main_option('script_location', str(ROOT/'migrations'))
                    self.assertEqual(ScriptDirectory.from_config(config).get_heads(), ['20261105_vehicle_documents'])
                finally:
                    db.session.remove()
                    db.engine.dispose()

    def test_mysql_ddl_constraints_and_seed(self):
        spec = importlib.util.spec_from_file_location('maintenance_migration', ROOT/'migrations/versions/20261029_vehicle_maintenance.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        buffer = io.StringIO()
        context = MigrationContext.configure(url='mysql+pymysql://', opts=dict(as_sql=True, output_buffer=buffer))
        with Operations.context(context):
            module.upgrade()
        for fragment in ['CREATE TABLE consumable_templates', 'CREATE TABLE vehicle_maintenance_items', 'CREATE TABLE vehicle_service_records', 'CREATE TABLE vehicle_defects', 'fk_defect_service', 'ck_vehicle_defect_dates', 'ON DELETE SET NULL', 'INSERT INTO consumable_templates', 'Масло ДВС и масляный фильтр', 'Смазка шкворня ССУ']:
            self.assertIn(fragment, buffer.getvalue())
