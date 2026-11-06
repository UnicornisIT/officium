import unittest
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError

from app import create_app
from app.models import ActivityLog, User
from extensions import db


class AdminUserDeleteAuditOrderTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                'TESTING': True,
                'ENVIRONMENT': 'testing',
                'SECRET_KEY': 'delete-audit-secret',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_ENGINE_OPTIONS': {},
                'WTF_CSRF_ENABLED': False,
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            admin = User(telegram_id=1, username='root', role='superadmin')
            member = User(telegram_id=2, username='member', role='user')
            db.session.add_all([admin, member])
            db.session.commit()
            self.admin_id = admin.id
            self.member_id = member.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _delete(self):
        return self.client.post(
            f'/admin/users/{self.member_id}',
            data={'action': 'delete', 'confirm_username': 'member'},
        )

    def test_failed_delete_has_no_success_audit(self):
        commit = db.session.commit

        def fail_delete_commit():
            if any(isinstance(item, User) for item in db.session.deleted):
                raise SQLAlchemyError('Deletion failed')
            return commit()

        with patch.object(db.session, 'commit', side_effect=fail_delete_commit):
            with self.assertLogs(self.app.logger, level='ERROR'):
                response = self._delete()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], f'/admin/users/{self.member_id}')
        with self.client.session_transaction() as session:
            self.assertIn('danger', [category for category, _ in session['_flashes']])
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(User, self.member_id))
            self.assertEqual(
                ActivityLog.query.filter(
                    ActivityLog.user_id == self.admin_id,
                    ActivityLog.action.contains('Удалил'),
                ).count(),
                0,
            )

    def test_successful_delete_preserves_audit_metadata(self):
        response = self._delete()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/admin/users')
        with self.app.app_context():
            self.assertIsNone(db.session.get(User, self.member_id))
            entry = ActivityLog.query.filter_by(action='Удалил пользователя').one()
            self.assertEqual(entry.user_id, self.admin_id)
            self.assertEqual(entry.entity_type, 'user')
            self.assertEqual(entry.entity_id, self.member_id)
            self.assertEqual(entry.description, 'Пользователь member')

    def test_failed_audit_commit_does_not_undo_delete(self):
        commit = db.session.commit

        def fail_audit_commit():
            if any(isinstance(item, ActivityLog) for item in db.session.new):
                raise SQLAlchemyError('Audit failed')
            return commit()

        with patch.object(db.session, 'commit', side_effect=fail_audit_commit):
            with self.assertLogs(self.app.logger, level='ERROR') as logs:
                response = self._delete()

        self.assertIn('Failed to record user deletion audit', '\n'.join(logs.output))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/admin/users')
        with self.client.session_transaction() as session:
            self.assertIn('success', [category for category, _ in session['_flashes']])
        with self.app.app_context():
            self.assertIsNone(db.session.get(User, self.member_id))
            self.assertEqual(ActivityLog.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
