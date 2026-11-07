import unittest
from unittest.mock import patch

import test_telegram_bot
from app.models import TelegramProcessedUpdate
from app.services import telegram_bot_service as bot
from extensions import db


class TelegramCleanupThrottlingTests(unittest.TestCase):
    setUp = test_telegram_bot.TelegramBotTestCase.setUp
    tearDown = test_telegram_bot.TelegramBotTestCase.tearDown
    _private_update = test_telegram_bot.TelegramBotTestCase._private_update

    def test_bulk_cleanup_runs_once_per_hour(self):
        with (
            self.app.app_context(),
            patch.object(bot, '_LAST_CLEANUP_AT', None),
            patch.object(bot.time, 'monotonic', return_value=0) as clock,
            patch.object(bot, '_cleanup_processed_updates') as processed,
            patch.object(bot, '_cleanup_expired_conversation_states') as expired,
        ):
            update = self._private_update('/help')
            for update_id in range(1, 101):
                clock.return_value = (update_id - 1) / 2
                bot.handle_telegram_update_once(dict(update, update_id=update_id))

            processed.assert_called_once_with()
            expired.assert_called_once_with()
            self.assertEqual(TelegramProcessedUpdate.query.count(), 100)

            clock.return_value = 3599
            bot.handle_telegram_update_once(dict(update, update_id=101))
            processed.assert_called_once_with()
            expired.assert_called_once_with()

            clock.return_value = 3600
            bot.handle_telegram_update_once(dict(update, update_id=102))
            self.assertEqual(processed.call_count, 2)
            self.assertEqual(expired.call_count, 2)
            self.assertIsNotNone(db.session.get(TelegramProcessedUpdate, 102))
