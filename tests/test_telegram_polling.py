import unittest
from threading import Event
from unittest.mock import patch

from app.models import TelegramProcessedUpdate
from app.services import telegram_bot_service as bot
from app.services import telegram_polling_service as polling
from app.services.telegram_transport import TelegramDeliveryError, TelegramDeliveryResult
import test_personal_settings


class TelegramPollingTests(unittest.TestCase):
    setUp = test_personal_settings.PersonalSettingsTests.setUp
    tearDown = test_personal_settings.PersonalSettingsTests.tearDown
    login = test_personal_settings.PersonalSettingsTests.login

    def update(self, update_id=1):
        return {'update_id': update_id, 'message': {
            'chat': {'id': 101, 'type': 'private'},
            'from': {'id': 101}, 'text': '/start',
        }}

    def run_batches(self, batches, *, webhook='', remove_webhook=False, delivery_error=None):
        self.app.config['TELEGRAM_BOT_ENABLED'] = True
        stop = Event()
        batches = iter(batches)

        def api(method, payload, **kwargs):
            if method == 'getWebhookInfo':
                return TelegramDeliveryResult(accepted=True), {'url': webhook}
            if method == 'deleteWebhook':
                return TelegramDeliveryResult(accepted=True), True
            batch = next(batches, None)
            if batch is None:
                stop.set()
                batch = []
            if isinstance(batch, TelegramDeliveryResult):
                return batch, None
            return TelegramDeliveryResult(accepted=True), batch

        with patch.object(polling, 'api_request', side_effect=api) as request, \
                patch.object(bot, 'send_telegram_message', return_value=True, side_effect=delivery_error) as sender, \
                patch.object(bot, 'answer_telegram_callback_query', return_value=True) as answer:
            polling.run_polling(remove_webhook=remove_webhook, stop=stop)
        return request, sender, answer

    def test_start_offset_and_replay_after_restart(self):
        request, sender, _ = self.run_batches([[self.update()]])
        sender.assert_called_once()
        self.assertIn('Выберите действие', sender.call_args.args[1])
        calls = [call for call in request.call_args_list if call.args[0] == 'getUpdates']
        self.assertNotIn('offset', calls[0].args[1])
        self.assertEqual(calls[1].args[1]['offset'], 2)
        self.assertGreater(calls[0].kwargs['timeout'][1], calls[0].args[1]['timeout'])
        _, sender, _ = self.run_batches([[self.update()]])
        sender.assert_not_called()
        self.assertEqual(TelegramProcessedUpdate.query.count(), 1)

    def test_callback_and_duplicate_in_same_batch(self):
        update = {'update_id': 4, 'callback_query': {
            'id': 'callback-4', 'from': {'id': 101}, 'data': 'tg:cancel',
            'message': {'chat': {'id': 101, 'type': 'private'}},
        }}
        _, sender, answer = self.run_batches([[update, update]])
        answer.assert_called_once_with('callback-4', 'Отменено')
        sender.assert_called_once()

    def test_switch_preserves_pending_commands_and_requires_explicit_flag(self):
        self.app.config['TELEGRAM_BOT_ENABLED'] = True
        with patch.object(polling, 'api_request', return_value=(
                TelegramDeliveryResult(accepted=True), {'url': 'https://example.test/webhook'})) as request:
            with self.assertRaisesRegex(ValueError, '--remove-webhook'):
                polling.run_polling()
            request.assert_called_once_with('getWebhookInfo', {})
        request, sender, _ = self.run_batches(
            [[self.update()]], webhook='https://example.test/webhook', remove_webhook=True)
        self.assertEqual(request.call_args_list[1].args,
                         ('deleteWebhook', {'drop_pending_updates': False}))
        sender.assert_called_once()

    def test_retry_does_not_advance_offset_on_processing_failure(self):
        original = polling.handle_telegram_update_once
        calls = 0

        def process(update):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError('private database detail')
            return original(update)

        with patch.object(polling, 'handle_telegram_update_once', side_effect=process), \
                patch.object(Event, 'wait', return_value=False) as wait, \
                self.assertLogs(self.app.logger, level='WARNING') as logs:
            request, sender, _ = self.run_batches([[self.update()], [self.update()]])
        self.assertNotIn('private database detail', str(logs.output))
        wait.assert_called_once_with(5)
        polls = [call for call in request.call_args_list if call.args[0] == 'getUpdates']
        self.assertNotIn('offset', polls[1].args[1])
        sender.assert_called_once()

    def test_network_and_rate_limit_retry(self):
        with patch.object(Event, 'wait', return_value=False) as wait:
            self.run_batches([
                TelegramDeliveryResult(failure_kind='connection'),
                TelegramDeliveryResult(failure_kind='rate_limited', retry_after=75),
                [self.update()],
            ])
        self.assertEqual([call.args[0] for call in wait.call_args_list], [5, 75])

    def test_conflict_and_invalid_token_stop(self):
        for kind in ('conflict', 'invalid_token'):
            with self.subTest(kind=kind), self.assertRaises(TelegramDeliveryError):
                self.run_batches([TelegramDeliveryResult(failure_kind=kind)])

    def test_malformed_updates_do_not_acknowledge_queue(self):
        for update in ({}, {'update_id': True}, {'update_id': -1}):
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, 'Invalid polling response'):
                self.run_batches([[update]])

    def test_webhook_removal_failure_never_polls(self):
        self.app.config['TELEGRAM_BOT_ENABLED'] = True
        responses = [(TelegramDeliveryResult(accepted=True), {'url': 'https://example.test/webhook'}),
                     (TelegramDeliveryResult(failure_kind='connection'), None)]
        with patch.object(polling, 'api_request', side_effect=responses) as request:
            with self.assertRaises(TelegramDeliveryError):
                polling.run_polling(remove_webhook=True)
        self.assertEqual([call.args[0] for call in request.call_args_list],
                         ['getWebhookInfo', 'deleteWebhook'])

    def test_uncertain_reply_is_not_sent_again(self):
        with self.assertLogs(self.app.logger, level='WARNING') as logs:
            request, sender, _ = self.run_batches(
                [[self.update()], [self.update()]],
                delivery_error=RuntimeError('private proxy secret'))
        self.assertNotIn('private proxy secret', str(logs.output))
        sender.assert_called_once()
        polls = [call for call in request.call_args_list if call.args[0] == 'getUpdates']
        self.assertEqual(polls[1].args[1]['offset'], 2)

    def test_missing_token_never_calls_api(self):
        for config in (
            {'TELEGRAM_BOT_ENABLED': False, 'TELEGRAM_BOT_TOKEN': ''},
            {'TELEGRAM_BOT_ENABLED': True, 'TELEGRAM_BOT_TOKEN': ''},
        ):
            with self.subTest(config=config), patch.dict(self.app.config, config), \
                    patch.object(polling, 'api_request') as request:
                with self.assertRaises(ValueError):
                    polling.run_polling()
                request.assert_not_called()

    def test_cli_reports_errors_without_traceback(self):
        self.app.config['TELEGRAM_BOT_TOKEN'] = ''
        result = self.app.test_cli_runner().invoke(args=['telegram-poll'])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('TELEGRAM_BOT_TOKEN', result.output)
        self.assertNotIn('Traceback', result.output)

    def test_cli_sigterm_sets_stop_and_restores_handler(self):
        import signal

        def run(**kwargs):
            self.assertFalse(kwargs['stop'].is_set())
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertTrue(kwargs['stop'].is_set())

        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(polling, 'run_polling', side_effect=run):
            result = self.app.test_cli_runner().invoke(args=['telegram-poll', '--remove-webhook'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
