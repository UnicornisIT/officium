import unittest

from app import app, create_app
import run


class AppImportTestCase(unittest.TestCase):
    def test_app_object_exists(self):
        self.assertIsNotNone(app)
        self.assertTrue(hasattr(app, 'run'))

    def test_create_app_factory_returns_flask_app(self):
        created_app = create_app()

        self.assertIsNotNone(created_app)
        self.assertTrue(hasattr(created_app, 'route'))

    def test_run_uses_package_app(self):
        self.assertIs(run.app, app)


if __name__ == '__main__':
    unittest.main()
