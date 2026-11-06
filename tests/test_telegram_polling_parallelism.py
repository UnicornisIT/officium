import unittest
from threading import Barrier, Event
from unittest.mock import patch

import test_telegram_polling
from app.services import telegram_polling_service as polling
from app.services.telegram_bot_service import TelegramBotResult
from extensions import db


class TelegramPollingParallelismTests(unittest.TestCase):
    setUp = test_telegram_polling.TelegramPollingTests.setUp
    tearDown = test_telegram_polling.TelegramPollingTests.tearDown
    login = test_telegram_polling.TelegramPollingTests.login
    update = test_telegram_polling.TelegramPollingTests.update
    run_batches = test_telegram_polling.TelegramPollingTests.run_batches

    def distinct_updates(self, count):
        updates = [self.update(i) for i in range(1, count + 1)]
        for i, update in enumerate(updates, start=1):
            update['message']['from']['id'] = i
            update['message']['chat']['id'] = i
        return updates

    def test_four_workers_overlap_with_independent_sessions(self):
        self.app.config['TELEGRAM_POLLING_WORKERS'] = 4
        barrier = Barrier(4, timeout=5)
        sessions = {}

        def process(update):
            update_id = update['update_id']
            session = db.session()
            if update_id <= 4:
                barrier.wait()
            sessions[update_id] = session
            return TelegramBotResult(chat_id=None, reply_text=None)

        with patch.object(polling, 'handle_telegram_update_once', side_effect=process):
            request, _, _ = self.run_batches([self.distinct_updates(5)])

        self.assertEqual(set(sessions), {1, 2, 3, 4, 5})
        self.assertEqual(len({sessions[i] for i in range(1, 5)}), 4)
        self.assertEqual(request.call_args.args[1]['offset'], 6)

    def test_offset_stops_at_failed_update_and_preserves_sender_order(self):
        first, failed, third = self.distinct_updates(3)
        same_sender = dict(failed, update_id=4)
        sender_updates = []

        def process(update):
            if update['message']['from']['id'] == 2:
                sender_updates.append(update['update_id'])
                if sender_updates == [2]:
                    raise RuntimeError('private database detail')
            return TelegramBotResult(chat_id=None, reply_text=None)

        stop = Event()
        with (
            patch.object(polling, 'handle_telegram_update_once', side_effect=process),
            patch.object(stop, 'wait', return_value=False) as retry,
        ):
            request, _, _ = self.run_batches(
                [
                    [same_sender, third, failed, first],
                    [same_sender, third, failed],
                ],
                stop=stop,
            )

        polls = [
            c.args[1].get('offset')
            for c in request.call_args_list
            if c.args[0] == 'getUpdates'
        ]
        self.assertEqual(polls, [None, 2, 5])
        self.assertEqual(sender_updates, [2, 2, 4])
        retry.assert_called_once_with(5)

    def test_stop_skips_remaining_updates_and_does_not_poll_again(self):
        self.app.config['TELEGRAM_POLLING_WORKERS'] = 1
        stop = Event()
        updates = self.distinct_updates(5)

        def process(update):
            stop.set()
            return TelegramBotResult(chat_id=None, reply_text=None)

        with patch.object(
            polling, 'handle_telegram_update_once', side_effect=process
        ) as handler:
            request, _, _ = self.run_batches([updates], stop=stop)

        handler.assert_called_once_with(updates[0])
        self.assertEqual(
            [c.args[0] for c in request.call_args_list],
            ['getWebhookInfo', 'getUpdates'],
        )
