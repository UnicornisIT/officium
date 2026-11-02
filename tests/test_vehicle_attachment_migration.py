import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask_migrate import upgrade, check
from sqlalchemy import inspect
from app import create_app
from extensions import db

ROOT = Path(__file__).resolve().parents[1]


class AttachmentMigrationTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('attachment_migration', ROOT/'migrations/versions/20261120_vehicle_attachments.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_mysql_and_forward_only(self):
        buffer = io.StringIO()
        context = MigrationContext.configure(url='mysql+pymysql://', opts=dict(as_sql=True, output_buffer=buffer))
        with Operations.context(context): self.module().upgrade()
        for fragment in ('CREATE TABLE vehicle_attachments', 'ON DELETE CASCADE', 'uq_attachment_sha256_user', 'ck_attachment_kind', 'ix_attachment_entity'):
            self.assertIn(fragment, buffer.getvalue())
        with self.assertRaises(RuntimeError): self.module().downgrade()

    def test_clean_sqlite_upgrade_matches_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch('logging.config.fileConfig'):
            app = create_app(dict(TESTING=True, SECRET_KEY='migration', SQLALCHEMY_DATABASE_URI='sqlite:///'+(Path(directory)/'test.sqlite').as_posix(), SQLALCHEMY_ENGINE_OPTIONS={}))
            with app.app_context():
                try:
                    upgrade(directory=str(ROOT/'migrations'))
                    self.assertIn('vehicle_attachments', inspect(db.engine).get_table_names())
                    check(directory=str(ROOT/'migrations'))
                finally:
                    db.session.remove()
                    db.engine.dispose()
