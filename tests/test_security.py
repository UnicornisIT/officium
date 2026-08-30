import unittest

from app import create_app
from app.models import User
from app.routes.admin import _csv_safe, _validated_settings
from extensions import db


class RuntimeSecurityTestCase(unittest.TestCase):
    @staticmethod
    def _production_config(**overrides):
        config = {
            'ENVIRONMENT': 'production',
            'SECRET_KEY': 'a-unique-production-secret-key-over-32-characters',
            'DEBUG': False,
            'DEV_LOGIN_ENABLED': False,
            'TEST_USER_ENABLED': False,
            'SESSION_COOKIE_SECURE': True,
            'TELEGRAM_LOGIN_ENABLED': False,
            'TELEGRAM_MINI_APP_ENABLED': False,
            'TELEGRAM_BOT_ENABLED': False,
            'GOOGLE_LOGIN_ENABLED': False,
            'ADMIN_LOGIN_ENABLED': False,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
        }
        config.update(overrides)
        return config

    def test_production_rejects_placeholder_secret(self):
        with self.assertRaisesRegex(RuntimeError, 'SECRET_KEY'):
            create_app(self._production_config(SECRET_KEY='change-me'))

    def test_production_requires_webhook_secret_for_enabled_bot(self):
        with self.assertRaisesRegex(RuntimeError, 'TELEGRAM_WEBHOOK_SECRET'):
            create_app(self._production_config(
                TELEGRAM_BOT_ENABLED=True,
                TELEGRAM_BOT_TOKEN='test-token',
                TELEGRAM_WEBHOOK_SECRET='',
            ))

    def test_production_rejects_development_features(self):
        for key in ('DEBUG', 'DEV_LOGIN_ENABLED', 'TEST_USER_ENABLED'):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, key):
                create_app(self._production_config(**{key: True}))

    def test_production_requires_secure_session_cookie(self):
        with self.assertRaisesRegex(RuntimeError, 'SESSION_COOKIE_SECURE'):
            create_app(self._production_config(SESSION_COOKIE_SECURE=False))

    def test_production_requires_token_for_telegram_features(self):
        with self.assertRaisesRegex(RuntimeError, 'TELEGRAM_BOT_TOKEN'):
            create_app(self._production_config(
                TELEGRAM_LOGIN_ENABLED=True,
                TELEGRAM_BOT_TOKEN='',
            ))

    def test_production_emergency_admin_login_requires_hash(self):
        with self.assertRaisesRegex(RuntimeError, 'password hash'):
            create_app(self._production_config(
                ADMIN_LOGIN_ENABLED=True,
                ADMIN_PASSWORD='plaintext-is-not-accepted-in-production',
                ADMIN_PASSWORD_HASH='',
            ))

    def test_security_headers_are_added(self):
        app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        client = app.test_client()
        with app.app_context():
            db.create_all()

        response = client.get('/login')

        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn('camera=()', response.headers['Permissions-Policy'])
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def test_error_pages_and_api_errors_do_not_expose_internal_details(self):
        app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        client = app.test_client()
        with app.app_context():
            db.create_all()
            user = User(telegram_id=-123456789, username='error-test', role='user')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

        page_response = client.get('/definitely-missing')
        api_response = client.get('/api/definitely-missing')

        self.assertEqual(page_response.status_code, 404)
        self.assertIn('Страница не найдена'.encode(), page_response.data)
        self.assertNotIn(b'Traceback', page_response.data)
        self.assertEqual(api_response.status_code, 404)
        self.assertEqual(api_response.get_json()['success'], False)
        self.assertNotIn('Traceback', api_response.get_json()['error'])
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def test_forged_legacy_test_session_is_rejected_when_feature_is_disabled(self):
        app = create_app({
            'TESTING': True,
            'TEST_USER_ENABLED': False,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        client = app.test_client()
        with app.app_context():
            db.create_all()
        with client.session_transaction() as session:
            session['_user_id'] = 'test-user'
            session['_fresh'] = True

        response = client.get('/api/debts')

        self.assertEqual(response.status_code, 401)
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def test_spreadsheet_formula_prefixes_are_neutralized(self):
        for value in ('=1+1', '+cmd', '-10+20', '@SUM(A1:A2)', '  =1+1'):
            with self.subTest(value=value):
                self.assertTrue(_csv_safe(value).startswith("'"))
        self.assertEqual(_csv_safe('ordinary text'), 'ordinary text')

    def test_admin_settings_reject_invalid_numeric_values(self):
        form = {
            'app_name': 'officium',
            'default_currency': 'RUB',
            'debt_limit_per_user': 'not-a-number',
            'payment_warning_days': '7',
            'urgent_payment_days': '3',
        }
        with self.assertRaisesRegex(ValueError, 'целым числом'):
            _validated_settings(form)

    def test_admin_settings_require_urgent_window_within_warning_window(self):
        form = {
            'app_name': 'officium',
            'default_currency': 'rub',
            'debt_limit_per_user': '50',
            'payment_warning_days': '3',
            'urgent_payment_days': '7',
        }
        with self.assertRaisesRegex(ValueError, 'не может превышать'):
            _validated_settings(form)

    def test_emergency_admin_session_can_be_restored_after_impersonation(self):
        app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'ADMIN_LOGIN_ENABLED': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        client = app.test_client()
        with app.app_context():
            db.create_all()
        with client.session_transaction() as session:
            session['_user_id'] = '1'
            session['_fresh'] = True
            session['original_admin_id'] = 'admin'

        response = client.post('/admin/stop-impersonate')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/admin'))
        with client.session_transaction() as session:
            self.assertEqual(session['_user_id'], 'admin')
        with app.app_context():
            db.session.remove()
            db.drop_all()


if __name__ == '__main__':
    unittest.main()
