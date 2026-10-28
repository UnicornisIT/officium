import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask_migrate import upgrade, downgrade, check
from sqlalchemy import inspect
from app import create_app
from extensions import db

ROOT=Path(__file__).resolve().parents[1]

class VehicleCostMigrationTests(unittest.TestCase):
    def test_sqlite_upgrade_downgrade_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch('logging.config.fileConfig'):
            app=create_app(dict(TESTING=True,SECRET_KEY='migration',SQLALCHEMY_DATABASE_URI='sqlite:///'+(Path(directory)/'test.sqlite').as_posix(),SQLALCHEMY_ENGINE_OPTIONS={}))
            with app.app_context():
                upgrade(directory=str(ROOT/'migrations'))
                self.assertIn('vehicle_costs',inspect(db.engine).get_table_names())
                check(directory=str(ROOT/'migrations'))
                downgrade(directory=str(ROOT/'migrations'),revision='20261027_vehicle_core')
                self.assertNotIn('vehicle_costs',inspect(db.engine).get_table_names())
                db.session.remove()
                db.engine.dispose()

    def test_mysql_ddl(self):
        spec=importlib.util.spec_from_file_location('cost_migration',ROOT/'migrations/versions/20261028_vehicle_costs.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        buffer=io.StringIO()
        context=MigrationContext.configure(url='mysql+pymysql://',opts=dict(as_sql=True,output_buffer=buffer))
        with Operations.context(context): module.upgrade()
        sql=buffer.getvalue()
        self.assertIn('NUMERIC(12, 2)',sql)
        self.assertIn('fk_vehicle_costs_vehicle',sql)
        self.assertIn('ck_vehicle_costs_amount',sql)
