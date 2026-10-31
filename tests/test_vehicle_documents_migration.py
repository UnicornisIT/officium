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

ROOT = Path(__file__).resolve().parents[1]


class DocumentMigrationTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('document_migration', ROOT/'migrations/versions/20261105_vehicle_documents.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_mysql_and_forward_only(self):
        buffer = io.StringIO()
        context = MigrationContext.configure(url='mysql+pymysql://', opts=dict(as_sql=True, output_buffer=buffer))
        with Operations.context(context): self.module().upgrade()
        for fragment in ['CREATE TABLE vehicle_documents', 'ON DELETE CASCADE', 'ON DELETE SET NULL', 'uq_vehicle_document_active_kind', 'ck_vehicle_document_dates']:
            self.assertIn(fragment, buffer.getvalue())
        with self.assertRaises(RuntimeError): self.module().downgrade()

    def test_upgrade_existing_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch('logging.config.fileConfig'):
            app = create_app(dict(TESTING=True, SECRET_KEY='migration', SQLALCHEMY_DATABASE_URI='sqlite:///'+(Path(directory)/'test.sqlite').as_posix(), SQLALCHEMY_ENGINE_OPTIONS={}))
            with app.app_context():
                try:
                    upgrade(directory=str(ROOT/'migrations'), revision='20261029_vehicle_fuel')
                    with db.engine.begin() as connection:
                        connection.execute(text("INSERT INTO users (id,telegram_id,role,is_blocked,created_at,login_count) VALUES (999,999,'user',0,CURRENT_TIMESTAMP,0)"))
                        connection.execute(text("INSERT INTO vehicles (id,user_id,name,kind,body_type,color,fuel_type,status,is_company,default_payer,mileage_reminder_days,created_at,updated_at) VALUES (999,999,'Existing','passenger','sedan','#5276ba','petrol','active',0,'self',7,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                    upgrade(directory=str(ROOT/'migrations'))
                    self.assertIn('vehicle_documents', inspect(db.engine).get_table_names())
                    check(directory=str(ROOT/'migrations'))
                finally:
                    db.session.remove()
                    db.engine.dispose()
