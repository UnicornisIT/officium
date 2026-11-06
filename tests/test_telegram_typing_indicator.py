import sqlite3
import unittest
from contextlib import closing
from unittest.mock import call, patch

import test_telegram_bot
from app import create_app
from app.services import telegram_bot_service as bot
from app.services.telegram_transport import TelegramDeliveryResult
from extensions import db


def test_typing_does_not_hold_sqlite_write_lock(tmp_path):
    database = tmp_path / 'typing.sqlite'
    app = create_app(
        {
            'TESTING': True,
            'SECRET_KEY': 'typing-test-secret',
            'SQLALCHEMY_DATABASE_URI': f'sqlite:///{database.as_posix()}',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'TELEGRAM_BOT_ENABLED': True,
            'TELEGRAM_BOT_TOKEN': 'test-token',
            'TELEGRAM_WEBHOOK_SECRET': 'test-secret',
        }
    )
    write_available = False

    def send_typing(*args, **kwargs):
        nonlocal write_available
        with closing(sqlite3.connect(database, timeout=0)) as connection:
            connection.execute('BEGIN IMMEDIATE')
            write_available = True
        return TelegramDeliveryResult(accepted=True), None

    with app.app_context():
        db.create_all()
        try:
            with patch.object(bot, 'api_request', side_effect=send_typing):
                bot.handle_telegram_update_once(
                    {
                        'update_id': 1,
                        'message': {
                            'chat': {'id': 101, 'type': 'private'},
                            'text': '/help',
                        },
                    }
                )
            assert write_available
        finally:
            db.session.remove()
            db.engine.dispose()


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
