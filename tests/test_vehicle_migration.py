"""Run the vehicle upgrade on SQLite and compile its actual DDL for MySQL."""
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask_migrate import upgrade
from sqlalchemy import inspect, text

from app import create_app
from extensions import db

ROOT = Path(__file__).resolve().parents[1]


class VehicleMigrationTestCase(unittest.TestCase):
    def setUp(self):
        # Alembic's CLI logging setup disables existing loggers. These upgrades
        # run inside the unittest process, so preserve the other suites' logging.
        logging_config = patch('logging.config.fileConfig')
        logging_config.start()
        self.addCleanup(logging_config.stop)

    def test_sqlite_upgrade_preserves_expenses_seeds_countries_and_set_null(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'vehicles.sqlite'
            app = create_app(dict(TESTING=True, SECRET_KEY='migration-test', SQLALCHEMY_DATABASE_URI='sqlite:///'+database.as_posix(), SQLALCHEMY_ENGINE_OPTIONS={}))
            with app.app_context():
                upgrade(directory=str(ROOT/'migrations'), revision='20261018_last_seen_release')
                with db.engine.begin() as conn:
                    conn.execute(text("INSERT INTO users (id, telegram_id, role, is_blocked, login_count, created_at) VALUES (1, 90001, 'user', 0, 0, CURRENT_TIMESTAMP)"))
                    conn.execute(text("INSERT INTO expenses (id,user_id,amount,category,title,expense_date,is_monthly) VALUES (1,1,123.45,'transport','Fictional expense','2026-01-01',0)"))
                upgrade(directory=str(ROOT/'migrations'))
                with db.engine.begin() as conn:
                    self.assertEqual(conn.execute(text('SELECT COUNT(*) FROM registration_countries')).scalar(),7)
                    self.assertTrue(conn.execute(text("SELECT strict_format FROM registration_countries WHERE code='RU'")).scalar())
                    self.assertEqual(conn.execute(text("SELECT name FROM registration_countries WHERE code='AB'")).scalar(),'Абхазия')
                    self.assertEqual(str(conn.execute(text('SELECT amount FROM expenses WHERE id=1')).scalar()),'123.45')
                    self.assertIsNone(conn.execute(text('SELECT vehicle_id FROM expenses WHERE id=1')).scalar())
                    conn.execute(text("INSERT INTO vehicles (id,user_id,name,kind,body_type,color,fuel_type,is_company,default_payer,status,mileage_reminder_days,created_at,updated_at) VALUES (1,1,'Fictional','passenger','sedan','#112233','petrol',0,'self','active',30,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                    conn.execute(text('UPDATE expenses SET vehicle_id=1'))
                with db.engine.connect() as conn:
                    conn.exec_driver_sql('PRAGMA foreign_keys=ON')
                    conn.execute(text('DELETE FROM vehicles WHERE id=1'))
                    self.assertIsNone(conn.execute(text('SELECT vehicle_id FROM expenses WHERE id=1')).scalar())
                    conn.commit()
                inspector=inspect(db.engine)
                self.assertIn('ix_expenses_vehicle_id',{index['name'] for index in inspector.get_indexes('expenses')})
                foreign=next(item for item in inspector.get_foreign_keys('vehicles') if item['constrained_columns']==['tractor_id'])
                self.assertEqual(foreign['options']['ondelete'],'SET NULL')
                db.session.remove()
                db.engine.dispose()

    def test_mysql_upgrade_ddl_has_named_constraints_and_seed(self):
        spec=importlib.util.spec_from_file_location('vehicle_migration',ROOT/'migrations/versions/20261027_vehicle_core.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        buffer=io.StringIO()
        context=MigrationContext.configure(url='mysql+pymysql://',opts=dict(as_sql=True,output_buffer=buffer))
        with Operations.context(context):
            module.upgrade()
        sql=buffer.getvalue()
        for fragment in ['CREATE TABLE vehicles','CREATE TABLE vehicle_odometer_readings','fk_expenses_vehicle_id','ON DELETE SET NULL','uq_vehicles_user_name','uq_vehicle_reading_date','INSERT INTO registration_countries','Абхазия']:
            self.assertIn(fragment,sql)
