import unittest

from app import create_app
from app.models import ActivityLog, User
from app.utils import EMERGENCY_ADMIN_LABEL
from extensions import db


class AuditLogIntegrityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'audit-log-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'ADMIN_LOGIN_ENABLED': True,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            root = User(telegram_id=1, username='root', role='superadmin')
            helper = User(telegram_id=2, username='helper', role='admin')
            member = User(telegram_id=3, username='member', role='user')
            db.session.add_all([root, helper, member])
            db.session.commit()
            self.root_id, self.helper_id, self.member_id = root.id, helper.id, member.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def test_deleting_an_admin_keeps_their_audit_trail(self):
        self._login(self.helper_id)
        self.client.post(f'/admin/users/{self.member_id}', data={'action': 'block'})
        with self.app.app_context():
            self.assertEqual(ActivityLog.query.filter_by(user_id=self.helper_id).count(), 1)

        self._login(self.root_id)
        self.client.post(f'/admin/users/{self.helper_id}', data={'action': 'delete', 'confirm_username': 'helper'})

        with self.app.app_context():
            self.assertIsNone(db.session.get(User, self.helper_id))
            entry = ActivityLog.query.filter_by(action='Заблокировал пользователя').one()
            self.assertIsNone(entry.user_id)
            self.assertEqual(entry.actor_label, f'helper #{self.helper_id}')

    def test_emergency_admin_actions_are_attributed(self):
        self._login('admin')

        self.client.post(f'/admin/users/{self.member_id}', data={'action': 'block'})

        with self.app.app_context():
            entry = ActivityLog.query.filter_by(action='Заблокировал пользователя').one()
            self.assertIsNone(entry.user_id)
            self.assertEqual(entry.actor_label, EMERGENCY_ADMIN_LABEL)

    def test_journal_shows_actor_of_deleted_account(self):
        with self.app.app_context():
            db.session.add(ActivityLog(action='Изменил настройки приложения', actor_label='ghost #99'))
            db.session.commit()
        self._login(self.root_id)

        html = self.client.get('/admin/logs').get_data(as_text=True)

        self.assertIn('ghost #99', html)


if __name__ == '__main__':
    unittest.main()
