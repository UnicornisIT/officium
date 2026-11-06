import unittest
from unittest.mock import call, patch

import test_telegram_bot
from app.services import telegram_bot_service as bot
from app.services.telegram_transport import TelegramDeliveryResult


class TelegramTypingIndicatorTests(unittest.TestCase):
    setUp = test_telegram_bot.TelegramBotTestCase.setUp
    tearDown = test_telegram_bot.TelegramBotTestCase.tearDown
    _private_update = test_telegram_bot.TelegramBotTestCase._private_update
    _callback_update = test_telegram_bot.TelegramBotTestCase._callback_update

    def send_update(self, update):
        response = self.client.post(
            '/telegram/webhook',
            json=update,
            headers={'X-Telegram-Bot-Api-Secret-Token': 'test-secret'},
        )
        self.assertEqual(response.status_code, 200)

    def test_typing_precedes_reply_and_duplicate_sends_nothing(self):
        self.app.config['TELEGRAM_BOT_RATE_LIMIT_PER_MINUTE'] = 1
        update = self._private_update('Расход')
        with patch.object(bot, '_RATE_LIMIT_BUCKETS', {}) as buckets:
            self.send_update(update)
            self.send_update(update)
            self.assertEqual(len(buckets[(123456, 123456)]), 1)

        typing, reply = self.telegram_api.call_args_list
        self.assertEqual(
            typing,
            call(
                'sendChatAction',
                {'chat_id': 123456, 'action': 'typing'},
                timeout=(2, 3),
            ),
        )
        self.assertEqual(reply.args[0], 'sendMessage')
        self.assertNotIn('Слишком много сообщений', reply.args[1]['text'])

    def test_callback_reply_survives_typing_failure(self):
        accepted = (TelegramDeliveryResult(accepted=True), None)
        self.telegram_api.side_effect = [
            RuntimeError('private detail'),
            accepted,
            accepted,
        ]

        with self.assertLogs(self.app.logger, level='DEBUG') as logs:
            self.send_update(self._callback_update('tg:cancel'))

        typing, answer, reply = self.telegram_api.call_args_list
        self.assertEqual(
            typing,
            call(
                'sendChatAction',
                {'chat_id': 123456, 'action': 'typing'},
                timeout=(2, 3),
            ),
        )
        self.assertEqual(answer.args[0], 'answerCallbackQuery')
        self.assertEqual(reply.args[0], 'sendMessage')
        self.assertIn('Telegram typing indicator failed.', logs.output[0])
        self.assertNotIn('private detail', '\n'.join(logs.output))
