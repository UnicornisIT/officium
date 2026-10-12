import unittest

from app import create_app
from app.models import User
from extensions import db


class ImpersonationSessionSecurityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'impersonation-security-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            admin = User(telegram_id=1, username='root', role='superadmin')
            target = User(telegram_id=2, username='target', role='user')
            other = User(telegram_id=3, username='other', role='user')
            db.session.add_all([admin, target, other])
            db.session.commit()
            self.admin_id, self.target_id, self.other_id = admin.id, target.id, other.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def _session_user(self):
        with self.client.session_transaction() as session:
            return session.get('_user_id')

    def _start_impersonation(self):
        self._login(self.admin_id)
        response = self.client.post(f'/admin/impersonate/{self.target_id}')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._session_user(), str(self.target_id))

    def test_stop_restores_admin_from_the_impersonated_session(self):
        self._start_impersonation()

        response = self.client.post('/admin/stop-impersonate')

        self.assertEqual(response.headers['Location'], '/admin')
        self.assertEqual(self._session_user(), str(self.admin_id))

    def test_logout_ends_impersonation_and_blocks_anonymous_restore(self):
        self._start_impersonation()
        self.client.get('/logout')

        with self.client.session_transaction() as session:
            self.assertNotIn('original_admin_id', session)
        self.client.post('/admin/stop-impersonate')

        self.assertIsNone(self._session_user())
        self.assertEqual(self.client.get('/admin').status_code, 302)

    def test_restore_requires_the_impersonated_user_to_be_signed_in(self):
        self._start_impersonation()
        # Another account takes over the same browser session.
        self._login(self.other_id)

        self.client.post('/admin/stop-impersonate')

        self.assertNotEqual(self._session_user(), str(self.admin_id))


if __name__ == '__main__':
    unittest.main()
