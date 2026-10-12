import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from app.models import ActivityLog
from app.routes.auth import EMERGENCY_LOGIN_FAILURE
from extensions import db


class EmergencyLoginLockoutTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'lockout-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'ADMIN_LOGIN_ENABLED': True,
            'ADMIN_PASSWORD_HASH': generate_password_hash('correct-horse'),
            'ADMIN_MAX_LOGIN_ATTEMPTS': 3,
            'ADMIN_LOCKOUT_MINUTES': 15,
        })
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _attempt(self, password, ip='192.0.2.10'):
        # A fresh client per attempt models an attacker who drops cookies.
        client = self.app.test_client()
        response = client.post(
            '/admin/login',
            data={'password': password},
            environ_base={'REMOTE_ADDR': ip},
        )
        return client, response

    def test_lockout_survives_cleared_cookies(self):
        for _ in range(3):
            self._attempt('wrong')

        client, response = self._attempt('correct-horse')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Слишком много неудачных попыток', response.get_data(as_text=True))
        with client.session_transaction() as session:
            self.assertNotEqual(session.get('_user_id'), 'admin')

    def test_lockout_is_per_address(self):
        for _ in range(3):
            self._attempt('wrong')

        _, response = self._attempt('correct-horse', ip='192.0.2.99')

        self.assertEqual(response.status_code, 302)

    def test_failures_are_logged_with_a_dedicated_action(self):
        self._attempt('wrong')

        with self.app.app_context():
            entry = ActivityLog.query.filter_by(action=EMERGENCY_LOGIN_FAILURE).one()
            self.assertEqual(entry.ip_address, '192.0.2.10')
            self.assertIn('1/3', entry.description)

    def test_successful_login_starts_a_new_window(self):
        self._attempt('wrong')
        self._attempt('wrong')
        _, response = self._attempt('correct-horse')
        self.assertEqual(response.status_code, 302)

        self._attempt('wrong')
        _, response = self._attempt('correct-horse')

        self.assertEqual(response.status_code, 302)


if __name__ == '__main__':
    unittest.main()
