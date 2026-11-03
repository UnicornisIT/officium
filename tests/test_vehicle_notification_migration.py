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

class NotificationMigrationTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('notifications_migration',ROOT/'migrations/versions/20261106_vehicle_notifications.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_mysql_forward_only(self):
        buffer=io.StringIO()
        context=MigrationContext.configure(url='mysql+pymysql://',opts=dict(as_sql=True,output_buffer=buffer))
        with Operations.context(context): self.module().upgrade()
        for fragment in ['CREATE TABLE vehicle_notification_log','ON DELETE CASCADE','ON DELETE SET NULL','uq_notification_log_day','DEFAULT true']:
            self.assertIn(fragment,buffer.getvalue())
        with self.assertRaises(RuntimeError): self.module().downgrade()

    def test_existing_user_upgrade(self):
        with tempfile.TemporaryDirectory() as directory, patch('logging.config.fileConfig'):
            app=create_app(dict(TESTING=True,SECRET_KEY='migration',SQLALCHEMY_DATABASE_URI='sqlite:///'+(Path(directory)/'test.sqlite').as_posix(),SQLALCHEMY_ENGINE_OPTIONS={}))
            with app.app_context():
                try:
                    upgrade(directory=str(ROOT/'migrations'),revision='20261105_vehicle_documents')
                    with db.engine.begin() as connection:
                        connection.execute(text("INSERT INTO users (id,telegram_id,role,is_blocked,created_at,login_count) VALUES (999,999,'user',0,CURRENT_TIMESTAMP,0)"))
                    upgrade(directory=str(ROOT/'migrations'))
                    self.assertIn('vehicle_notification_log',inspect(db.engine).get_table_names())
                    self.assertEqual(db.session.execute(text('SELECT vehicle_notifications_enabled FROM users WHERE id=999')).scalar(),1)
                    row = db.session.execute(text('SELECT hide_amounts_on_open, notification_timezone, notification_delivery_time, notification_test_requested_at FROM users WHERE id=999')).one()
                    self.assertEqual(tuple(row), (0, None, None, None))
                    check(directory=str(ROOT/'migrations'))
                finally:
                    db.session.remove()
                    db.engine.dispose()

    def test_personal_settings_mysql_ddl(self):
        spec = importlib.util.spec_from_file_location('personal_settings_migration', ROOT/'migrations/versions/20261122_personal_settings.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        buffer = io.StringIO()
        context = MigrationContext.configure(url='mysql+pymysql://', opts=dict(as_sql=True, output_buffer=buffer))
        with Operations.context(context):
            module.upgrade()
        for fragment in ['notification_timezone VARCHAR(64)', 'notification_delivery_time VARCHAR(5)',
                         'notification_test_requested_at DATETIME', 'hide_amounts_on_open BOOL NOT NULL DEFAULT false']:
            self.assertIn(fragment, buffer.getvalue())
