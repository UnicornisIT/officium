import unittest
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError

from app import create_app
from app.models import ActivityLog, DictionaryEntry, User
from extensions import db


class AdminDictionaryAuditFailureTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                'TESTING': True,
                'ENVIRONMENT': 'testing',
                'SECRET_KEY': 'dictionary-audit-secret',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_ENGINE_OPTIONS': {},
                'WTF_CSRF_ENABLED': False,
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            admin = User(telegram_id=1, username='root', role='superadmin')
            entry = DictionaryEntry(dictionary_type='bank', value='Original bank')
            db.session.add_all([admin, entry])
            db.session.commit()
            admin_id, self.entry_id = admin.id, entry.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(admin_id)
            session['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _assert_save_survives_audit_failure(self, action, failure):
        if failure == 'value_error':
            audit_patch = patch(
                'app.routes.admin.record_activity',
                side_effect=ValueError('Audit failed'),
            )
        else:
            commit = db.session.commit

            def fail_audit_commit():
                if any(isinstance(item, ActivityLog) for item in db.session.new):
                    raise SQLAlchemyError('Audit failed')
                return commit()

            audit_patch = patch.object(
                db.session, 'commit', side_effect=fail_audit_commit
            )

        if action == 'create':
            url = '/admin/dictionaries'
            data = {'dictionary_type': 'bank', 'value': 'New bank'}
        else:
            url = f'/admin/dictionaries/{self.entry_id}/update'
            data = {'action': action, 'value': 'New bank'}

        with audit_patch, self.assertLogs(self.app.logger, level='ERROR') as logs:
            response = self.client.post(url, data=data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/admin/dictionaries')
        with self.client.session_transaction() as session:
            self.assertEqual(
                [category for category, _ in session['_flashes']], ['success']
            )
        with self.app.app_context():
            if action == 'toggle':
                self.assertFalse(
                    db.session.get(DictionaryEntry, self.entry_id).is_active
                )
            else:
                entry = DictionaryEntry.query.filter_by(value='New bank').one()
                if action == 'rename':
                    self.assertEqual(entry.id, self.entry_id)
            self.assertEqual(ActivityLog.query.count(), 0)
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].getMessage(), 'audit recording failed')
        self.assertIsNotNone(logs.records[0].exc_info)

    def test_create_survives_audit_value_error(self):
        self._assert_save_survives_audit_failure('create', 'value_error')

    def test_rename_survives_audit_value_error(self):
        self._assert_save_survives_audit_failure('rename', 'value_error')

    def test_toggle_survives_audit_value_error(self):
        self._assert_save_survives_audit_failure('toggle', 'value_error')

    def test_create_survives_audit_commit_error(self):
        self._assert_save_survives_audit_failure('create', 'commit_error')

    def test_rename_survives_audit_commit_error(self):
        self._assert_save_survives_audit_failure('rename', 'commit_error')

    def test_toggle_survives_audit_commit_error(self):
        self._assert_save_survives_audit_failure('toggle', 'commit_error')


if __name__ == '__main__':
    unittest.main()
