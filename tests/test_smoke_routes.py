import unittest

from app import create_app


class SmokeRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                'TESTING': True,
                'ENVIRONMENT': 'testing',
                'SECRET_KEY': 'smoke-route-test-secret',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'WTF_CSRF_ENABLED': False,
            }
        )
        self.client = self.app.test_client()

    def test_public_entry_routes_do_not_return_server_errors(self):
        for path in ('/', '/login', '/admin/login'):
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertLess(response.status_code, 500)


if __name__ == '__main__':
    unittest.main()
