import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import test_telegram_bot
from app.models import TelegramProcessedUpdate
from extensions import db


class TelegramAsyncDeliveryTests(unittest.TestCase):
    setUp = test_telegram_bot.TelegramBotTestCase.setUp
    tearDown = test_telegram_bot.TelegramBotTestCase.tearDown
    _private_update = test_telegram_bot.TelegramBotTestCase._private_update

    def post_update(self):
        return self.client.post(
            '/telegram/webhook',
            json=self._private_update('Расход'),
            headers={'X-Telegram-Bot-Api-Secret-Token': 'test-secret'},
        )

    def test_webhook_acknowledges_before_deferred_delivery(self):
        submit = self.app.extensions['telegram_delivery_executor'].submit
        submit.side_effect = None

        response = self.post_update()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'ok': True})
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(TelegramProcessedUpdate, 1))
        self.assertEqual(
            [c.args[0] for c in self.telegram_api.call_args_list], ['sendChatAction']
        )
        submit.assert_called_once()
        submit.call_args.args[0]()
        self.assertEqual(self.telegram_api.call_args.args[0], 'sendMessage')

        self.assertEqual(self.post_update().status_code, 200)
        submit.assert_called_once()

    def test_webhook_does_not_wait_for_failing_send(self):
        started = Event()
        release = Event()

        def send_reply(*args, **kwargs):
            started.set()
            if not release.wait(timeout=5):
                self.fail('Webhook did not acknowledge while delivery was blocked')
            raise RuntimeError('private network detail')

        with (
            ThreadPoolExecutor(max_workers=1) as executor,
            patch.dict(self.app.extensions, telegram_delivery_executor=executor),
            patch(
                'app.routes.telegram_bot.send_telegram_message', side_effect=send_reply
            ),
            self.assertLogs(self.app.logger, level='WARNING') as logs,
        ):
            try:
                self.assertEqual(self.post_update().status_code, 200)
                self.assertTrue(started.wait(timeout=5))
                self.assertEqual(logs.output, [])
            finally:
                release.set()
            executor.shutdown(wait=True)

        self.assertIn('Telegram reply delivery failed.', '\n'.join(logs.output))
        self.assertNotIn('private network detail', '\n'.join(logs.output))
