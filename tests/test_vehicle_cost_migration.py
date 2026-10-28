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
    def test_sqlite_upgrade_preserves_history_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch('logging.config.fileConfig'):
            app=create_app(dict(TESTING=True,SECRET_KEY='migration',SQLALCHEMY_DATABASE_URI='sqlite:///'+(Path(directory)/'test.sqlite').as_posix(),SQLALCHEMY_ENGINE_OPTIONS={}))
            with app.app_context():
                upgrade(directory=str(ROOT/'migrations'))
                self.assertIn('vehicle_costs',inspect(db.engine).get_table_names())
                check(directory=str(ROOT/'migrations'))
                with self.assertRaises(SystemExit) as denied:
                    downgrade(directory=str(ROOT/'migrations'),revision='20261027_vehicle_core')
                self.assertEqual(denied.exception.code, 1)
                self.assertIn('vehicle_costs',inspect(db.engine).get_table_names())
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

    def test_detail_migration_preserves_legacy_costs(self):
        from sqlalchemy import text
        with tempfile.TemporaryDirectory() as directory, patch('logging.config.fileConfig'):
            app=create_app(dict(TESTING=True,SECRET_KEY='migration',SQLALCHEMY_DATABASE_URI='sqlite:///'+(Path(directory)/'test.sqlite').as_posix(),SQLALCHEMY_ENGINE_OPTIONS={}))
            with app.app_context():
                try:
                    upgrade(directory=str(ROOT/'migrations'),revision='20261028_vehicle_costs')
                    with db.engine.begin() as conn:
                        conn.execute(text("INSERT INTO users(id,telegram_id,role,is_blocked,login_count,created_at) VALUES(1,99001,'user',0,0,CURRENT_TIMESTAMP)"))
                        conn.execute(text("INSERT INTO vehicles(id,user_id,name,kind,body_type,color,fuel_type,is_company,default_payer,status,mileage_reminder_days,created_at,updated_at) VALUES(1,1,'Fictional','passenger','sedan','#112233','petrol',1,'organization','active',30,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                        conn.execute(text("INSERT INTO vehicle_costs(id,user_id,vehicle_id,cost_date,amount,kind,paid_by_driver,created_at,updated_at) VALUES(1,1,1,'2026-03-15',123.45,'fuel',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                    upgrade(directory=str(ROOT/'migrations'))
                    with db.engine.connect() as conn:
                        row=conn.execute(text('SELECT amount,title,payment_method FROM vehicle_costs WHERE id=1')).one()
                        self.assertEqual(str(row.amount),'123.45')
                        self.assertIsNone(row.title)
                        self.assertIsNone(row.payment_method)
                    check(directory=str(ROOT/'migrations'))
                finally:
                    db.session.remove(); db.engine.dispose()

    def test_detail_mysql_ddl(self):
        spec=importlib.util.spec_from_file_location('details_migration',ROOT/'migrations/versions/20261028_vehicle_cost_details.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        buffer=io.StringIO()
        context=MigrationContext.configure(url='mysql+pymysql://',opts=dict(as_sql=True,output_buffer=buffer))
        with Operations.context(context): module.upgrade()
        self.assertIn('ADD COLUMN title VARCHAR(150)',buffer.getvalue())
        self.assertIn('ADD COLUMN payment_method VARCHAR(20)',buffer.getvalue())
