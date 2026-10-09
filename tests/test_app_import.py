import unittest
from pathlib import Path

from app import create_app


class AppImportTestCase(unittest.TestCase):
    def test_create_app_factory_returns_flask_app(self):
        created_app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'app-factory-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
        })

        self.assertIsNotNone(created_app)
        self.assertTrue(hasattr(created_app, 'route'))

    def test_default_production_app_requires_secret(self):
        with self.assertRaisesRegex(RuntimeError, 'SECRET_KEY'):
            create_app({
                'ENVIRONMENT': 'production',
                'SECRET_KEY': '',
                'TESTING': False,
                'DEBUG': False,
                'DEV_LOGIN_ENABLED': False,
                'TEST_USER_ENABLED': False,
                'SESSION_COOKIE_SECURE': True,
            })

    def test_run_builds_app_from_factory(self):
        run_source = (Path(__file__).resolve().parents[1] / 'run.py').read_text(encoding='utf-8')

        self.assertIn('from app import create_app', run_source)
        self.assertIn('app = create_app()', run_source)


if __name__ == '__main__':
    unittest.main()
