import hashlib
import hmac
import json
import re
import time
import unittest
from urllib.parse import urlencode

from app import create_app
from app.models import User
from app.services.telegram_auth_service import verify_telegram_web_app_init_data
from extensions import db


BOT_TOKEN = '123456:test-token'


def build_init_data(user=None, auth_date=None, **extra):
    profile = user or {
        'id': 987654321,
        'first_name': 'Ева',
        'last_name': 'Тестова',
        'username': 'eva_test',
        'photo_url': 'https://example.com/avatar.jpg',
    }
    data = {
        'auth_date': str(auth_date or int(time.time())),
        'query_id': 'AAHdF6IQAAAAAN0XohDhrOrc',
        'user': json.dumps(profile, ensure_ascii=False, separators=(',', ':')),
        **{key: str(value) for key, value in extra.items()},
    }
    data_check_string = '\n'.join(f'{key}={data[key]}' for key in sorted(data))
    secret_key = hmac.new(b'WebAppData', BOT_TOKEN.encode('utf-8'), hashlib.sha256).digest()
    data['hash'] = hmac.new(
        secret_key,
        data_check_string.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(data)


class TelegramMiniAppAuthServiceTestCase(unittest.TestCase):
    def test_accepts_valid_init_data(self):
        parsed = verify_telegram_web_app_init_data(build_init_data(), BOT_TOKEN)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['user']['id'], 987654321)
        self.assertEqual(parsed['user']['first_name'], 'Ева')

    def test_rejects_tampered_init_data(self):
        parsed = verify_telegram_web_app_init_data(
            build_init_data() + '&start_param=tampered',
            BOT_TOKEN,
        )

        self.assertIsNone(parsed)

    def test_rejects_expired_init_data(self):
        parsed = verify_telegram_web_app_init_data(
            build_init_data(auth_date=int(time.time()) - 90000),
            BOT_TOKEN,
            max_age_seconds=86400,
        )

        self.assertIsNone(parsed)

    def test_rejects_duplicate_fields(self):
        parsed = verify_telegram_web_app_init_data(
            build_init_data() + '&auth_date=1',
            BOT_TOKEN,
        )

        self.assertIsNone(parsed)


class TelegramMiniAppRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'TELEGRAM_BOT_TOKEN': BOT_TOKEN,
            'TELEGRAM_BOT_USERNAME': 'officium_test_bot',
            'TELEGRAM_MINI_APP_ENABLED': True,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_entry_page_is_available_without_web_session(self):
        response = self.client.get('/telegram-app')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Готовим ваш финансовый центр', response.get_data(as_text=True))

    def test_login_works_with_production_csrf_protection(self):
        self.app.config['WTF_CSRF_ENABLED'] = True
        entry = self.client.get('/telegram-app')
        match = re.search(r'<meta name="csrf-token" content="([^"]+)"', entry.get_data(as_text=True))
        self.assertIsNotNone(match)

        response = self.client.post(
            '/auth/telegram-mini-app',
            json={'init_data': build_init_data()},
            headers={'X-CSRFToken': match.group(1)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])

    def test_valid_init_data_creates_user_and_session(self):
        response = self.client.post(
            '/auth/telegram-mini-app',
            json={'init_data': build_init_data()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        with self.app.app_context():
            user = User.query.filter_by(telegram_id=987654321).one()
            self.assertEqual(user.first_name, 'Ева')
            self.assertEqual(user.login_count, 1)
        with self.client.session_transaction() as flask_session:
            self.assertTrue(flask_session['telegram_mini_app'])
            self.assertIsNotNone(flask_session.get('_user_id'))

        dashboard = self.client.get('/')
        html = dashboard.get_data(as_text=True)
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn('class="telegram-mini-app"', html)
        self.assertIn('class="telegram-bottom-nav"', html)
        self.assertNotIn('class="site-footer', html)

        for path in ('/incomes', '/expenses', '/mortgages', '/finance', '/financial-plan', '/archive'):
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200)
                self.assertIn('class="telegram-bottom-nav"', page.get_data(as_text=True))

    def test_invalid_init_data_is_rejected(self):
        response = self.client.post(
            '/auth/telegram-mini-app',
            json={'init_data': 'user=invalid&hash=invalid'},
        )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json()['success'])

    def test_blocked_user_cannot_enter(self):
        with self.app.app_context():
            db.session.add(User(
                telegram_id=987654321,
                first_name='Blocked',
                role='user',
                is_blocked=True,
            ))
            db.session.commit()

        response = self.client.post(
            '/auth/telegram-mini-app',
            json={'init_data': build_init_data()},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()['success'])


if __name__ == '__main__':
    unittest.main()
