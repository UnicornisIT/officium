import hashlib
import hmac
import re
import time
import unittest
from pathlib import Path

from app import create_app
from app.models import AppSetting, User
from app.services.telegram_auth_service import telegram_bot_id
from extensions import db


BOT_TOKEN = '123456:test-token-secret-part'
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def signed_login_data(telegram_id=987654321, auth_date=None, **profile):
    """Fields exactly as Telegram.Login.auth() hands them to the browser."""
    data = {
        'id': str(telegram_id),
        'first_name': 'Ева',
        'username': 'eva_test',
        'auth_date': str(auth_date or int(time.time())),
        **profile,
    }
    data_check_string = '\n'.join(f'{key}={data[key]}' for key in sorted(data))
    secret_key = hashlib.sha256(BOT_TOKEN.encode('utf-8')).digest()
    data['hash'] = hmac.new(secret_key, data_check_string.encode('utf-8'), hashlib.sha256).hexdigest()
    return data


class TelegramBotIdTestCase(unittest.TestCase):
    def test_reads_public_numeric_prefix(self):
        self.assertEqual(telegram_bot_id(BOT_TOKEN), 123456)

    def test_rejects_malformed_tokens(self):
        for token in ('', None, 'no-colon', ':secret', 'abc:secret', '0:secret', '123456:', '-5:secret'):
            with self.subTest(token=token):
                self.assertIsNone(telegram_bot_id(token))


class TelegramLoginTestCase(unittest.TestCase):
    config = {}

    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'telegram-login-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'TELEGRAM_BOT_TOKEN': BOT_TOKEN,
            'TELEGRAM_BOT_USERNAME': 'officium_test_bot',
            'TELEGRAM_LOGIN_ENABLED': True,
            'TELEGRAM_MINI_APP_ENABLED': True,
            'ADMIN_TELEGRAM_IDS': ['555'],
            **self.config,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add(AppSetting(key='registration_enabled', value='true'))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login_page(self):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    @staticmethod
    def launcher_tag(html):
        match = re.search(r'<script src="[^"]*/js/telegram-login\.js[^"]*"([^>]*)></script>', html)
        return match.group(1) if match else None


class TelegramLoginLauncherMarkupTestCase(TelegramLoginTestCase):
    def test_login_button_starts_the_shared_launcher_without_embedded_widget(self):
        html = self.login_page()

        button = re.search(r'<button id="telegram-login-loader"[^>]*>', html).group(0)
        self.assertIn('data-telegram-login-trigger', button)
        # The exact classes keep the blue pill button pixel-identical.
        self.assertIn('class="btn btn-info btn-lg rounded-pill px-4 text-white"', button)
        self.assertEqual(html.count('Войти через Telegram'), 1)
        # No second, embedded Telegram widget gets injected any more.
        self.assertNotIn('data-telegram-login=', html)
        self.assertNotIn('dataset.telegramLogin', html)
        self.assertIn('id="telegram-login-status"', html)

    def test_header_sign_in_link_uses_the_same_launcher(self):
        html = self.login_page()

        link = re.search(r'<a class="nav-link nav-expand" href="/login"[^>]*>', html).group(0)
        self.assertIn('data-telegram-login-trigger', link)
        self.assertEqual(html.count('data-telegram-login-trigger'), 2)

        tag = self.launcher_tag(html)
        self.assertIsNotNone(tag)
        self.assertIn('data-available="true"', tag)
        self.assertIn('data-bot-id="123456"', tag)
        self.assertIn('data-auth-url="/telegram-login"', tag)

    def test_launcher_is_on_other_public_pages_too(self):
        html = self.client.get('/documentation').get_data(as_text=True)

        self.assertIsNotNone(self.launcher_tag(html))
        self.assertIn('data-telegram-login-trigger', html)

    def test_bot_token_secret_never_reaches_the_page(self):
        html = self.login_page()

        self.assertNotIn('test-token-secret-part', html)
        self.assertNotIn(BOT_TOKEN, html)

    def test_authenticated_and_mini_app_pages_do_not_load_the_launcher(self):
        with self.app.app_context():
            user = User(telegram_id=42, role='user')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
        html = self.client.get('/').get_data(as_text=True)

        self.assertIsNone(self.launcher_tag(html))
        self.assertNotIn('data-telegram-login-trigger', html)

    def test_launcher_script_uses_the_official_api_only(self):
        source = (PROJECT_ROOT / 'static' / 'js' / 'telegram-login.js').read_text(encoding='utf-8')

        self.assertIn('https://telegram.org/js/telegram-widget.js', source)
        self.assertIn('Telegram.Login.auth(authOptions, finish)', source)
        self.assertIn('Telegram.Login.init(authOptions, finish)', source)
        # Never fake a click on Telegram's iframe.
        self.assertNotIn('iframe', source.split('*/', 1)[1])
        self.assertNotIn('dispatchEvent', source)
        self.assertNotIn('.click()', source)


class TelegramLoginDisabledMarkupTestCase(TelegramLoginTestCase):
    config = {'TELEGRAM_LOGIN_ENABLED': False}

    def test_disabled_login_marks_notice_and_keeps_header_link(self):
        html = self.login_page()

        self.assertIn('Вход через Telegram отключён.', html)
        self.assertIn('data-telegram-login-unavailable', html)
        self.assertNotIn('id="telegram-login-loader"', html)
        tag = self.launcher_tag(html)
        self.assertIn('data-available="false"', tag)
        self.assertIn('data-bot-id=""', tag)
        self.assertIn('href="/login" data-telegram-login-trigger', html)


class TelegramLoginCallbackTestCase(TelegramLoginTestCase):
    def test_signed_data_creates_session_and_lands_on_dashboard(self):
        response = self.client.get('/telegram-login', query_string=signed_login_data())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')
        with self.app.app_context():
            user = User.query.filter_by(telegram_id=987654321).one()
            self.assertEqual(user.username, 'eva_test')
            self.assertEqual(user.login_count, 1)
        with self.client.session_transaction() as session:
            self.assertEqual(session.get('_user_id'), str(user.id))
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_admin_lands_on_admin_panel(self):
        response = self.client.get('/telegram-login', query_string=signed_login_data(telegram_id=555))

        self.assertEqual(response.headers['Location'], '/admin')

    def test_photo_and_last_name_fields_stay_signed(self):
        data = signed_login_data(last_name='Тестова', photo_url='https://t.me/i/userpic/320/eva.jpg')

        response = self.client.get('/telegram-login', query_string=data)

        self.assertEqual(response.status_code, 302)

    def test_tampered_data_is_rejected(self):
        data = signed_login_data()
        data['id'] = '1'

        response = self.client.get('/telegram-login', query_string=data)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Ошибка авторизации через Telegram', response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            self.assertNotIn('_user_id', session)

    def test_expired_data_is_rejected(self):
        response = self.client.get('/telegram-login', query_string=signed_login_data(auth_date=int(time.time()) - 90000))

        self.assertIn('Ошибка авторизации через Telegram', response.get_data(as_text=True))

    def test_blocked_user_gets_no_session(self):
        with self.app.app_context():
            db.session.add(User(telegram_id=987654321, role='user', is_blocked=True))
            db.session.commit()

        response = self.client.get('/telegram-login', query_string=signed_login_data())

        self.assertEqual(response.status_code, 200)
        self.assertIn('Ваш аккаунт заблокирован', response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            self.assertNotIn('_user_id', session)

    def test_closed_registration_still_blocks_new_users_only(self):
        with self.app.app_context():
            AppSetting.query.filter_by(key='registration_enabled').one().value = 'false'
            db.session.add(User(telegram_id=111, role='user'))
            db.session.commit()

        new_user = self.client.get('/telegram-login', query_string=signed_login_data())
        self.assertIn('Регистрация новых пользователей временно отключена', new_user.get_data(as_text=True))

        existing = self.client.get('/telegram-login', query_string=signed_login_data(telegram_id=111))
        self.assertEqual(existing.headers['Location'], '/')

    def test_disabled_telegram_login_rejects_callback(self):
        with self.app.app_context():
            db.session.add(AppSetting(key='telegram_login_enabled', value='false'))
            db.session.commit()

        response = self.client.get('/telegram-login', query_string=signed_login_data())

        self.assertIn('Вход через Telegram временно отключён администратором', response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            self.assertNotIn('_user_id', session)


if __name__ == '__main__':
    unittest.main()
