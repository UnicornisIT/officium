import unittest

from app import create_app
from app.models import ActivityLog
from app.utils import client_ip
from extensions import db


class ClientIpTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'client-ip-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'ADMIN_LOGIN_ENABLED': True,
            'ADMIN_PASSWORD_HASH': 'pbkdf2:sha256:1$invalid$invalid',
        })
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_forwarded_header_from_client_is_ignored(self):
        with self.app.test_request_context(
            '/',
            headers={'X-Forwarded-For': '6.6.6.6'},
            environ_base={'REMOTE_ADDR': '203.0.113.7'},
        ):
            self.assertEqual(client_ip(), '203.0.113.7')

    def test_value_fits_the_log_column(self):
        with self.app.test_request_context('/', environ_base={'REMOTE_ADDR': 'x' * 500}):
            self.assertEqual(len(client_ip()), 100)

    def test_failed_admin_login_logs_the_real_peer(self):
        client = self.app.test_client()
        client.post(
            '/admin/login',
            data={'password': 'wrong'},
            headers={'X-Forwarded-For': '6.6.6.6, ' + '9' * 300},
            environ_base={'REMOTE_ADDR': '198.51.100.20'},
        )

        with self.app.app_context():
            entry = ActivityLog.query.order_by(ActivityLog.id.desc()).first()
            self.assertEqual(entry.ip_address, '198.51.100.20')


if __name__ == '__main__':
    unittest.main()
