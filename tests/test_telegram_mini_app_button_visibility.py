import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

from test_telegram_mini_app import BOT_TOKEN, build_init_data

from app import create_app
from app.models import TelegramConversationState, User
from app.services.telegram_bot_service import (
    _main_menu_keyboard,
    process_telegram_text_result,
)
from app.services import telegram_mini_app_check_service as mini_check
from app.services.telegram_transport import TelegramDeliveryResult
from app.utils import telegram_feature_states
from extensions import db


class TelegramMiniAppButtonVisibilityTestCase(unittest.TestCase):
    def setUp(self):
        mini_check._cache.clear()
        self.addCleanup(mini_check._cache.clear)
        self.app = create_app(
            {
                'TESTING': True,
                'SECRET_KEY': 'mini-app-visibility-test',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_ENGINE_OPTIONS': {},
                'WTF_CSRF_ENABLED': False,
                'TELEGRAM_LOGIN_ENABLED': True,
                'TELEGRAM_MINI_APP_ENABLED': True,
                'TELEGRAM_BOT_TOKEN': 'visibility-test-token',
                'TELEGRAM_BOT_USERNAME': 'visibility_test_bot',
                'OFFICIUM_PUBLIC_URL': 'https://example.com',
            }
        )
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def has_button(self):
        return any(
            button.get('text') == 'Открыть Mini App'
            for row in _main_menu_keyboard()['keyboard']
            for button in row
        )

    def test_missing_or_invalid_public_url_hides_button_and_invalidates_state(self):
        for url in (
            '',
            None,
            'http://example.com',
            'invalid',
            'https://user:password@example.com',
            'https://example.com?query=1',
            'https://example.com:invalid',
            'https://example.com:99999',
        ):
            with self.subTest(url=url), self.app.app_context():
                self.app.config['OFFICIUM_PUBLIC_URL'] = url
                self.assertFalse(self.has_button())
                state = telegram_feature_states(self.app.config)['mini_app']
                self.assertFalse(state['configuration_valid'])
                self.assertFalse(state['public_url_ready'])
                self.assertFalse(state['effective'])

    def test_unregistered_menu_hides_button(self):
        with patch('app.services.telegram_transport.requests.Session.post') as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {
                'ok': True,
                'result': {'type': 'commands'},
            }
            with self.app.app_context():
                self.assertFalse(self.has_button())

    def test_matching_registered_menu_shows_button(self):
        with patch('app.services.telegram_transport.requests.Session.post') as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {
                'ok': True,
                'result': {
                    'type': 'web_app',
                    'web_app': {'url': 'https://example.com/telegram-app'},
                },
            }
            with self.app.app_context():
                self.assertTrue(self.has_button())
            self.assertTrue(request.call_args.args[0].endswith('/getChatMenuButton'))
            self.assertEqual(request.call_args.kwargs['json'], {})

    def test_menu_entry_does_not_launch_without_authentication_data(self):
        with self.app.app_context(), patch(
            'app.services.telegram_bot_service.check_mini_app_registered',
            return_value=True,
        ):
            keyboard = _main_menu_keyboard()
        buttons = [button for row in keyboard['keyboard'] for button in row]
        self.assertIn({'text': 'Открыть Mini App'}, buttons)
        self.assertFalse(any('web_app' in button for button in buttons))

    def test_menu_entry_returns_inline_launcher_without_clearing_input(self):
        with self.app.app_context(), patch(
            'app.services.telegram_bot_service.check_mini_app_registered',
            return_value=True,
        ):
            user = User(telegram_id=916001, username='mini_app_user')
            db.session.add(user)
            db.session.commit()
            process_telegram_text_result(user, 'Расход')
            state = TelegramConversationState.query.one()
            state_id, step, data = state.id, state.step, state.data

            result = process_telegram_text_result(user, 'Открыть Mini App')

            self.assertEqual(result.reply_markup, {
                'inline_keyboard': [[{
                    'text': 'Открыть Mini App',
                    'web_app': {'url': 'https://example.com/telegram-app'},
                }]],
            })
            self.assertNotIn('keyboard', result.reply_markup)
            db.session.expire_all()
            state = TelegramConversationState.query.one()
            self.assertEqual((state.id, state.step, state.data), (state_id, step, data))

    def test_stale_menu_entry_rechecks_registration_and_feature_setting(self):
        with self.app.app_context(), patch(
            'app.services.telegram_bot_service.check_mini_app_registered',
            return_value=False,
        ) as check:
            user = User(telegram_id=916001, username='mini_app_user')
            db.session.add(user)
            db.session.commit()
            for enabled, registered in ((True, False), (False, True)):
                with self.subTest(enabled=enabled, registered=registered):
                    self.app.config['TELEGRAM_MINI_APP_ENABLED'] = enabled
                    check.return_value = registered
                    result = process_telegram_text_result(user, 'Открыть Mini App')
                    self.assertIn('недоступен', result.reply_text)
                    self.assertNotIn('inline_keyboard', result.reply_markup)
                    self.assertFalse(any(
                        'web_app' in button
                        for row in result.reply_markup['keyboard'] for button in row
                    ))

    def test_inline_launch_supports_signed_login(self):
        self.app.config['TELEGRAM_BOT_TOKEN'] = BOT_TOKEN
        with self.app.app_context(), patch(
            'app.services.telegram_bot_service.check_mini_app_registered',
            return_value=True,
        ):
            user = User(telegram_id=916001, username='mini_app_user')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
            result = process_telegram_text_result(user, 'Открыть Mini App')
            url = result.reply_markup['inline_keyboard'][0][0]['web_app']['url']

        client = self.app.test_client()
        self.assertEqual(client.get(urlsplit(url).path).status_code, 200)
        response = client.post('/auth/telegram-mini-app', json={
            'init_data': build_init_data(user={'id': 916001, 'first_name': 'Mini App'}),
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        with client.session_transaction() as session:
            self.assertEqual(session['_user_id'], str(user_id))
            self.assertTrue(session['telegram_mini_app'])

    def test_mismatched_or_unavailable_menu_hides_button(self):
        for menu in (
            None,
            {},
            {'type': 'default'},
            {'type': 'web_app'},
            {'type': 'web_app', 'web_app': {'url': 'https://other.test/telegram-app'}},
            {'type': 'web_app', 'web_app': {'url': 'https://example.com/other'}},
        ):
            with self.subTest(menu=menu), self.app.app_context(), patch.object(
                mini_check,
                'api_request',
                return_value=(TelegramDeliveryResult(accepted=True), menu),
            ):
                mini_check._cache.clear()
                self.assertFalse(self.has_button())
        with self.app.app_context(), patch.object(
            mini_check,
            'api_request',
            return_value=(TelegramDeliveryResult(error_code=404), None),
        ):
            mini_check._cache.clear()
            self.assertFalse(self.has_button())

    def test_cache_expires_and_separates_tokens_and_urls(self):
        for accepted in (True, False):
            with self.subTest(accepted=accepted), self.app.app_context(), patch.object(
                mini_check,
                'api_request',
                return_value=(
                    TelegramDeliveryResult(accepted=accepted),
                    {
                        'type': 'web_app',
                        'web_app': {'url': 'https://example.com/telegram-app'},
                    },
                ),
            ) as request, patch.object(
                mini_check, 'monotonic', return_value=0
            ) as clock:
                mini_check._cache.clear()
                check = mini_check.check_mini_app_registered
                self.assertEqual(check('token-one', 'https://example.com'), accepted)
                clock.return_value = 3599
                self.assertEqual(check('token-one', 'https://example.com/'), accepted)
                self.assertEqual(request.call_count, 1)
                clock.return_value = 3600
                self.assertEqual(check('token-one', 'https://example.com'), accepted)
                self.assertEqual(request.call_count, 2)
                check('token-two', 'https://example.com')
                check('token-one', 'https://other.test')
                self.assertEqual(request.call_count, 4)

    def test_disabled_feature_does_not_request_telegram(self):
        for setting in ('TELEGRAM_MINI_APP_ENABLED', 'TELEGRAM_LOGIN_ENABLED'):
            with self.subTest(setting=setting), self.app.app_context(), patch.object(
                mini_check,
                'api_request',
            ) as request:
                original = self.app.config[setting]
                self.app.config[setting] = False
                self.assertFalse(self.has_button())
                request.assert_not_called()
                self.app.config[setting] = original

    def test_startup_logs_missing_public_url(self):
        from app import _validate_runtime_config

        self.app.config['OFFICIUM_PUBLIC_URL'] = ''
        with self.assertLogs(self.app.logger, level='ERROR') as captured:
            _validate_runtime_config(self.app)
        self.assertIn('OFFICIUM_PUBLIC_URL', captured.output[0])
        self.assertIn('@BotFather', captured.output[0])

    def test_admin_settings_explains_menu_status(self):
        with self.app.app_context():
            admin = User(telegram_id=-916, username='root', role='superadmin')
            db.session.add(admin)
            db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(admin_id)
            session['_fresh'] = True
        for registered in (False, True):
            with self.subTest(registered=registered), patch(
                'app.routes.admin.check_mini_app_registered',
                return_value=registered,
            ):
                response = client.get('/admin/settings')
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn(
                    (
                        'URL Mini App подтверждён в меню Telegram'
                        if registered
                        else 'Зарегистрируйте домен в @BotFather'
                    ),
                    html,
                )
                if not registered:
                    self.assertIn('https://example.com/telegram-app', html)
