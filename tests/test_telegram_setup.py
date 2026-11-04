import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services import telegram_setup_service as setup
from app.services.telegram_transport import TelegramDeliveryError, TelegramDeliveryResult
import test_personal_settings


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('telegram_services', ROOT / 'deployment/telegram_services.py')
services = importlib.util.module_from_spec(spec)
spec.loader.exec_module(services)


class TelegramSetupTests(unittest.TestCase):
    setUp = test_personal_settings.PersonalSettingsTests.setUp
    tearDown = test_personal_settings.PersonalSettingsTests.tearDown
    login = test_personal_settings.PersonalSettingsTests.login

    def configure(self, mode):
        self.app.config.update(TELEGRAM_BOT_ENABLED=True, TELEGRAM_RECEIVE_MODE=mode,
                               TELEGRAM_WEBHOOK_SECRET='private-webhook-secret')

    def responses(self):
        return [(TelegramDeliveryResult(accepted=True), {'is_bot': True, 'username': 'officium_test_bot'}),
                (TelegramDeliveryResult(accepted=True), True)]

    def test_polling_switch_keeps_queue(self):
        self.configure('polling')
        with patch.object(setup, 'api_request', side_effect=self.responses()) as request:
            state = setup.setup()
        self.assertEqual(state['receive_mode'], 'polling')
        self.assertEqual(request.call_args.args, ('deleteWebhook', {'drop_pending_updates': False}))

    def test_webhook_sets_configured_url_and_secret(self):
        self.configure('webhook')
        with patch.object(setup, 'api_request', side_effect=self.responses()) as request:
            setup.setup()
        method, payload = request.call_args.args
        self.assertEqual(method, 'setWebhook')
        self.assertEqual(payload['url'], 'https://example.test/telegram/webhook')
        self.assertEqual(payload['secret_token'], 'private-webhook-secret')
        self.assertFalse(payload['drop_pending_updates'])

    def test_dry_run_disabled_bot_and_cli_are_private(self):
        self.configure('polling')
        with patch.object(setup, 'api_request') as request:
            result = self.app.test_cli_runner().invoke(args=['telegram-setup', '--dry-run'])
            self.assertEqual(result.exit_code, 0, result.output)
            state = json.loads(result.output)
            self.assertTrue(state['bot_enabled'])
            self.assertNotIn('offline-token', result.output)
            self.assertNotIn('private-webhook-secret', result.output)
            self.app.config['TELEGRAM_BOT_ENABLED'] = False
            self.app.config['TELEGRAM_BOT_TOKEN'] = ''
            self.assertFalse(setup.setup()['bot_enabled'])
            request.assert_not_called()

    def test_notifications_only_validate_token_without_changing_webhook(self):
        self.app.config['TELEGRAM_BOT_ENABLED'] = False
        with patch.object(setup, 'api_request', return_value=self.responses()[0]) as request:
            self.assertTrue(setup.setup()['monthly_reminders'])
        request.assert_called_once_with('getMe', {})

    def test_invalid_configuration_does_not_change_webhook(self):
        self.configure('webhook')
        for values in ({'TELEGRAM_RECEIVE_MODE': 'other'}, {'OFFICIUM_PUBLIC_URL': 'http://example.test'},
                       {'TELEGRAM_WEBHOOK_SECRET': ''}, {'TELEGRAM_BOT_TOKEN': ''}):
            with self.subTest(values=list(values)), patch.dict(self.app.config, values), \
                    patch.object(setup, 'api_request') as request:
                with self.assertRaises(ValueError):
                    setup.setup()
                request.assert_not_called()

    def test_wrong_bot_and_network_failure_do_not_change_webhook(self):
        self.configure('polling')
        for response, error in (((TelegramDeliveryResult(accepted=True), {'is_bot': True, 'username': 'different'}), ValueError),
                                ((TelegramDeliveryResult(failure_kind='connection'), None), TelegramDeliveryError)):
            with self.subTest(error=error), patch.object(setup, 'api_request', return_value=response) as request:
                with self.assertRaises(error):
                    setup.setup()
                request.assert_called_once_with('getMe', {})


class TelegramServiceDeploymentTests(unittest.TestCase):
    def deploy(self, state):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        deployment = root / 'current' / 'deployment'
        deployment.mkdir(parents=True)
        unit_dir = root / 'units'
        unit_dir.mkdir()
        env_file = root / 'shared' / '.env'
        env_file.parent.mkdir()
        original = 'HTTPS_PROXY=http://existing:password@proxy.example:3128\nNO_PROXY=localhost,custom.internal\n'
        env_file.write_text(original, encoding='utf-8')
        for name in ('officium-telegram-polling.service', 'officium-monthly-reminders.service', 'officium-monthly-reminders.timer'):
            (deployment / name).write_text((ROOT / 'deployment' / name).read_text(encoding='utf-8'), encoding='utf-8')

        def run(command, label, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(state) if 'telegram-setup' in command else '')

        with patch.object(services, '_run', side_effect=run) as request, \
                patch.object(services.shutil, 'which', side_effect=lambda name: name):
            result = services.sync(root, 'service_user', 'service_group', 'custom', '.venv', unit_dir=unit_dir)
        self.assertEqual(env_file.read_text(encoding='utf-8'), original)
        self.assertEqual(result, state)
        poll = (unit_dir / 'custom-telegram-polling.service').read_text(encoding='utf-8')
        self.assertNotIn('@@', poll)
        self.assertIn('User=service_user', poll)
        self.assertIn('/current/.venv/bin/python -m flask', poll)
        self.assertIn('PartOf=custom.service', poll)
        return [call.args[0] for call in request.call_args_list]

    def test_first_install_polling_and_monthly_timer(self):
        calls = self.deploy({'bot_enabled': True, 'receive_mode': 'polling', 'monthly_reminders': True})
        self.assertIn(['systemctl', 'enable', '--now', 'custom-telegram-polling.service'], calls)
        self.assertIn(['systemctl', 'enable', '--now', 'custom-monthly-reminders.timer'], calls)
        stop = next(index for index, call in enumerate(calls) if call[:2] == ['systemctl', 'stop'])
        setup_index = next(index for index, call in enumerate(
            calls) if 'telegram-setup' in call and '--dry-run' not in call)
        self.assertLess(stop, setup_index)

    def test_mode_change_webhook_and_disabled_services(self):
        calls = self.deploy({'bot_enabled': True, 'receive_mode': 'webhook', 'monthly_reminders': False})
        self.assertIn(['systemctl', 'disable', '--now', 'custom-telegram-polling.service'], calls)
        self.assertIn(['systemctl', 'disable', '--now', 'custom-monthly-reminders.timer'], calls)

    def test_credentials_and_raw_child_errors_are_not_exposed(self):
        with patch.object(services.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout='private-token', stderr='private-proxy-password')):
            with self.assertRaisesRegex(RuntimeError, '^Telegram setup failed.$'):
                services._run(['command'], 'Telegram setup')
