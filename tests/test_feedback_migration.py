from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import inspect, text

from app import create_app
from extensions import db


class FeedbackMigrationTests(unittest.TestCase):
    def test_upgrade_schema_check_and_downgrade_preserve_existing_user(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            'logging.config.fileConfig'
        ):
            app = create_app(
                dict(
                    TESTING=True,
                    SECRET_KEY='feedback-migration',
                    SQLALCHEMY_DATABASE_URI='sqlite:///'
                    + (Path(directory) / 'app.sqlite').as_posix(),
                    SQLALCHEMY_ENGINE_OPTIONS={},
                )
            )
            runner = app.test_cli_runner()
            result = runner.invoke(args=['db', 'upgrade', '20261124_monthly_reminders'])
            self.assertEqual(result.exit_code, 0, result.output)
            with app.app_context(), db.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (telegram_id, created_at, role, is_blocked, login_count) "
                        "VALUES (12345, CURRENT_TIMESTAMP, 'user', 0, 0)"
                    )
                )
            result = runner.invoke(args=['db', 'upgrade'])
            self.assertEqual(result.exit_code, 0, result.output)
            with app.app_context():
                self.assertIn('feedback_reports', inspect(db.engine).get_table_names())
            result = runner.invoke(args=['db', 'check'])
            self.assertEqual(result.exit_code, 0, result.output)
            result = runner.invoke(
                args=['db', 'downgrade', '20261124_monthly_reminders']
            )
            self.assertEqual(result.exit_code, 0, result.output)
            with app.app_context(), db.engine.connect() as connection:
                self.assertNotIn(
                    'feedback_reports', inspect(connection).get_table_names()
                )
                self.assertEqual(
                    connection.execute(text('SELECT COUNT(*) FROM users')).scalar_one(),
                    1,
                )
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
