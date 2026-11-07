"""Offline transport, UI and operator diagnostics privacy regressions."""
import json
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

import requests

from app.models import AppSetting
from app.services import telegram_bot_service as bot
from app.services import telegram_transport as transport
from app.services import telegram_diagnostics_service as diagnostics
from app.services.telegram_transport import TelegramDeliveryError
from extensions import db
import test_personal_settings


class TelegramDeliveryTests(unittest.TestCase):
    setUp = test_personal_settings.PersonalSettingsTests.setUp
    tearDown = test_personal_settings.PersonalSettingsTests.tearDown
    login = test_personal_settings.PersonalSettingsTests.login
    secret = 'private-token-do-not-log'

    def response(self, status=200, data=None):
        return Mock(status_code=status, json=Mock(return_value=data))

    def test_delivery_classification_and_private_errors(self):
        cases = [
            (400, {'description': 'Bad Request: chat not found'}, 'chat_unavailable'),
            (400, {'description': 'Bad Request: other private content'}, 'bad_request'),
            (401, {}, 'invalid_token'), (404, {}, 'invalid_token'),
            (403, {}, 'blocked'), (429, {'parameters': {'retry_after': 95}}, 'rate_limited'),
            (409, {}, 'conflict'),
            (500, {}, 'telegram_unavailable'), (502, {}, 'telegram_unavailable'),
            (200, {}, 'invalid_response'), (200, {'ok': 'true'}, 'invalid_response'),
        ]
        for code, extra, kind in cases:
            data = dict(ok=False, error_code=code, description=self.secret)
            if code == 200:
                data.pop('error_code')
            data.update(extra)
            with self.subTest(code=code, kind=kind), patch.object(transport.requests.Session, 'post',
                    return_value=self.response(code, data)) as post:
                with self.assertLogs(self.app.logger, level='WARNING') as logs:
                    with self.assertRaises(TelegramDeliveryError) as caught:
                        bot.send_telegram_message(123456789, 'private amount 99999', bot_token=self.secret)
                result = caught.exception.result
                self.assertEqual(result.failure_kind, kind)
                self.assertFalse(result.accepted)
                if kind == 'rate_limited':
                    self.assertEqual(result.retry_after, 95)
                output = str(logs.output) + str(caught.exception)
                for private in (self.secret, '123456789', '99999', 'api.telegram.org'):
                    self.assertNotIn(private, output)
                self.assertEqual(post.call_count, 1)

    def test_success_requires_api_true_and_http_success(self):
        with patch.object(transport.requests.Session, 'post', return_value=self.response(200,
                {'ok': True, 'result': {'message_id': 42}})):
            self.assertIs(bot.send_telegram_message(1, 'test'), True)
            self.assertIs(bot.answer_telegram_callback_query('callback'), True)
        for data in (None, [], 'private', {'ok': False}, {'ok': 1}, {'ok': 'true'}):
            with self.subTest(data=data), patch.object(transport.requests.Session, 'post', return_value=self.response(200, data)):
                with self.assertRaises(TelegramDeliveryError):
                    bot.send_telegram_message(1, 'test')
        with patch.object(transport.requests.Session, 'post', return_value=self.response(302, {'ok': True})):
            with self.assertRaises(TelegramDeliveryError):
                bot.send_telegram_message(1, 'test')

    def test_network_and_invalid_json_do_not_leak_or_retry(self):
        for error, kind in [(requests.Timeout, 'timeout'), (requests.ConnectionError, 'connection'),
                            (requests.exceptions.SSLError, 'tls'), (requests.RequestException, 'network')]:
            with self.subTest(kind=kind), patch.object(transport.requests.Session, 'post',
                    side_effect=error(f'https://api.telegram.org/bot{self.secret}/sendMessage')) as post:
                with self.assertLogs(self.app.logger, level='WARNING') as logs:
                    with self.assertRaises(TelegramDeliveryError) as caught:
                        bot.send_telegram_message(1, 'private')
                self.assertEqual(caught.exception.result.failure_kind, kind)
                self.assertNotIn(self.secret, str(logs.output) + str(caught.exception))
                post.assert_called_once()
        response = self.response()
        response.json.side_effect = ValueError(self.secret)
        with patch.object(transport.requests.Session, 'post', return_value=response):
            with self.assertRaises(TelegramDeliveryError) as caught:
                bot.send_telegram_message(1, 'private')
            self.assertEqual(caught.exception.result.failure_kind, 'invalid_response')
            self.assertNotIn(self.secret, str(caught.exception))

    def test_profile_safe_specific_errors_and_global_flag_independence(self):
        self.app.config['VEHICLE_NOTIFICATIONS_ENABLED'] = False
        cases = [(400, {'description': 'chat not found'}, 'Запустить', 502),
                 (401, {}, 'администратору', 502), (403, {}, 'Разблокируйте', 502),
                 (429, {'parameters': {'retry_after': 95}}, '95 секунд', 429),
                 (500, {}, 'временно недоступен', 502)]
        for code, extra, expected, status in cases:
            self.user.notification_test_requested_at = None
            db.session.commit()
            data = dict(ok=False, error_code=code, description=self.secret)
            data.update(extra)
            with self.subTest(code=code), patch.object(transport.requests.Session, 'post',
                    return_value=self.response(code, data)) as post:
                response = self.client.post('/profile/test-notification', data={'user_id': self.other.id})
                self.assertEqual(response.status_code, status)
                self.assertIn(expected, response.get_data(as_text=True))
                self.assertNotIn(self.secret, response.get_data(as_text=True))
                self.assertEqual(post.call_args.kwargs['json']['chat_id'], self.user.telegram_id)
                self.assertEqual(self.client.post('/profile/test-notification').status_code, 429)
                post.assert_called_once()
        self.user.notification_test_requested_at -= timedelta(seconds=60)
        db.session.commit()
        with patch.object(transport.requests.Session, 'post', return_value=self.response(200, {'ok': True})):
            self.assertEqual(self.client.post('/profile/test-notification').status_code, 303)

    def test_diagnostics_explicit_sanitized_and_shared_cooldown(self):
        self.app.config.update(TELEGRAM_BOT_TOKEN=self.secret, TELEGRAM_WEBHOOK_SECRET=self.secret,
                               TELEGRAM_BOT_ENABLED=False)
        runner = self.app.test_cli_runner()
        with patch.object(transport.requests.Session, 'post') as post:
            result = runner.invoke(args=['telegram-diagnostics'])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(json.loads(result.output)['network'], 'not_requested')
            post.assert_not_called()
        responses = [self.response(200, {'ok': True, 'result': {
            'is_bot': True, 'username': 'officium_test_bot', 'id': 123456789}}),
            self.response(200, {'ok': True, 'result': {
                'url': 'https://example.test/telegram/webhook', 'pending_update_count': 2,
                'last_error_message': self.secret, 'ip_address': self.secret}})]
        with patch.object(transport.requests.Session, 'post', side_effect=responses) as post:
            result = runner.invoke(args=['telegram-diagnostics', '--network'])
            self.assertEqual(result.exit_code, 0, result.output)
            report = json.loads(result.output)
            self.assertTrue(report['getMe']['username_matches'])
            self.assertTrue(report['getWebhookInfo']['url_matches'])
            self.assertTrue(report['getWebhookInfo']['has_last_error'])
            self.assertFalse(report['TELEGRAM_BOT_ENABLED'])
            self.assertNotIn(self.secret, result.output)
            self.assertNotIn('123456789', result.output)
            self.assertNotIn('https://', result.output)
            self.assertEqual(post.call_count, 2)
            db.session.remove()  # New session / worker still sees reservation.
            self.assertEqual(diagnostics.diagnose(network=True)['network'], 'cooldown_60_seconds')
            self.assertEqual(post.call_count, 2)
        row = AppSetting.query.filter_by(key='telegram_diagnostics_requested_at').one()
        row.value = '0'
        db.session.commit()
        responses[0].json.return_value['result']['username'] = 'different_bot'
        with patch.object(transport.requests.Session, 'post', side_effect=responses):
            self.assertFalse(diagnostics.diagnose(network=True)['getMe']['username_matches'])

    def test_diagnostics_reads_saved_bot_switch_and_receiver_configuration(self):
        self.app.config.update(
            TELEGRAM_BOT_ENABLED=False,
            TELEGRAM_RECEIVE_MODE='webhook',
            TELEGRAM_WEBHOOK_SECRET='test-secret',
        )
        db.session.add(AppSetting(key='telegram_bot_enabled', value='true'))
        db.session.commit()
        report = diagnostics.diagnose()
        self.assertFalse(report['TELEGRAM_BOT_ENABLED'])
        self.assertTrue(report['bot_commands_enabled'])
        self.assertTrue(report['receiver_configured'])
        self.app.config['OFFICIUM_PUBLIC_URL'] = ''
        report = diagnostics.diagnose()
        self.assertFalse(report['receiver_configured'])
        self.assertEqual(report['receiver_configuration_error'], 'invalid_public_url')

    def test_webhook_failure_identifies_http_status_without_exposing_description(self):
        self.app.config.update(
            TELEGRAM_RECEIVE_MODE='webhook', TELEGRAM_WEBHOOK_SECRET='test-secret'
        )
        responses = [
            self.response(
                200,
                {
                    'ok': True,
                    'result': {'is_bot': True, 'username': 'officium_test_bot'},
                },
            ),
            self.response(
                200,
                {
                    'ok': True,
                    'result': {
                        'url': 'https://example.test/telegram/webhook',
                        'pending_update_count': 8,
                        'last_error_message': 'Wrong response from the webhook: 403 Forbidden '
                        + self.secret,
                    },
                },
            ),
        ]
        with patch.object(transport.requests.Session, 'post', side_effect=responses):
            report = diagnostics.diagnose(network=True)
        self.assertTrue(report['getWebhookInfo']['receive_mode_matches'])
        self.assertTrue(report['getWebhookInfo']['has_last_error'])
        self.assertEqual(report['getWebhookInfo']['last_error_kind'], 'http_error')
        self.assertEqual(report['getWebhookInfo']['last_error_http_status'], 403)
        self.assertEqual(report['getWebhookInfo']['pending_update_count'], 8)
        self.assertNotIn(self.secret, json.dumps(report))
        self.assertNotIn('https://', json.dumps(report))

    def test_reminders_false_and_exception_counted_without_secret(self):
        for sender in (Mock(return_value=False), Mock(side_effect=requests.HTTPError(
                f'https://api.telegram.org/bot{self.secret}/sendMessage'))):
            with self.subTest(sender=sender), patch.object(bot, 'build_debt_reminder_message', return_value='private'):
                with self.assertLogs(self.app.logger, level='WARNING') as logs:
                    stats = bot.send_debt_reminders(sender=sender)
                self.assertEqual(stats['errors'], 2)
                self.assertNotIn(self.secret, str(logs.output))

    def test_diagnostics_missing_token_and_api_failure_are_safe(self):
        self.app.config['TELEGRAM_BOT_TOKEN'] = ''
        with patch.object(transport.requests.Session, 'post') as post:
            self.assertEqual(diagnostics.diagnose(network=True)['network'], 'not_configured')
            post.assert_not_called()
        self.assertIsNone(AppSetting.query.filter_by(key='telegram_diagnostics_requested_at').first())
        self.app.config['TELEGRAM_BOT_TOKEN'] = self.secret
        with patch.object(transport.requests.Session, 'post', side_effect=requests.Timeout(self.secret)) as post:
            with self.assertLogs(self.app.logger, level='WARNING') as logs:
                report = diagnostics.diagnose(network=True)
            self.assertEqual(report['getMe']['failure_kind'], 'timeout')
            self.assertEqual(report['getWebhookInfo']['failure_kind'], 'timeout')
            self.assertNotIn(self.secret, json.dumps(report) + str(logs.output))
            self.assertEqual(post.call_count, 2)

    def test_reminder_cli_reports_failed_batch(self):
        self.app.config['TELEGRAM_BOT_ENABLED'] = True
        with patch.object(bot, 'send_debt_reminders', return_value=dict(users_checked=2, messages=2, errors=1)):
            result = self.app.test_cli_runner().invoke(args=['send-telegram-reminders'])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('Errors: 1', result.output)
