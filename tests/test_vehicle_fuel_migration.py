import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask_migrate import upgrade, check
from sqlalchemy import inspect, text
from app import create_app
from extensions import db

ROOT=Path(__file__).resolve().parents[1]


class FuelMigrationTests(unittest.TestCase):
    def test_sqlite_upgrade_existing_history_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch('logging.config.fileConfig'):
            app=create_app(dict(TESTING=True,SECRET_KEY='migration',SQLALCHEMY_DATABASE_URI='sqlite:///'+(Path(directory)/'test.sqlite').as_posix(),SQLALCHEMY_ENGINE_OPTIONS={}))
            with app.app_context():
                try:
                    upgrade(directory=str(ROOT/'migrations'),revision='20261029_vehicle_maintenance')
                    with db.engine.begin() as conn:
                        conn.execute(text("INSERT INTO users (id,telegram_id,role,is_blocked,created_at,login_count) VALUES (9001,9001,'user',0,CURRENT_TIMESTAMP,0)"))
                        conn.execute(text("INSERT INTO vehicles (id,user_id,name,kind,body_type,color,fuel_type,is_company,default_payer,status,mileage_reminder_days,created_at,updated_at) VALUES (9001,9001,'Fixture','passenger','sedan','#5276ba','petrol',0,'self','active',30,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                        conn.execute(text("INSERT INTO vehicle_costs (id,vehicle_id,user_id,cost_date,amount,kind,paid_by_driver,created_at,updated_at) VALUES (9001,9001,9001,'2026-03-01',100,'fuel',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                    upgrade(directory=str(ROOT/'migrations'))
                    self.assertIn('vehicle_refuelings',inspect(db.engine).get_table_names())
                    with db.engine.connect() as conn:
                        self.assertEqual(conn.execute(text('SELECT COUNT(*) FROM vehicles WHERE id=9001')).scalar(),1)
                        self.assertEqual(conn.execute(text('SELECT amount FROM vehicle_costs WHERE id=9001')).scalar(),100)
                    check(directory=str(ROOT/'migrations'))
                finally:
                    db.session.remove()
                    db.engine.dispose()

    def test_mysql_ddl_and_forward_only(self):
        spec=importlib.util.spec_from_file_location('fuel_migration', ROOT/'migrations/versions/20261029_vehicle_fuel.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output=io.StringIO()
        context=MigrationContext.configure(url='mysql+pymysql://',opts=dict(as_sql=True,output_buffer=output))
        with Operations.context(context): module.upgrade()
        for fragment in ['NUMERIC(10, 3)','NUMERIC(12, 2)','ON DELETE CASCADE','ON DELETE SET NULL','uq_vehicle_refueling_date','ck_refueling_payer']:
            self.assertIn(fragment,output.getvalue())
        with self.assertRaisesRegex(RuntimeError,'preserve refueling history'): module.downgrade()
