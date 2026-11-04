import unittest
from datetime import timedelta
from unittest.mock import patch

from app.models import Expense, FeedbackReport, TelegramConversationState, User
from app.time_utils import utc_now
from extensions import db
import test_telegram_bot


class FeedbackBotTests(unittest.TestCase):
    _private_update = test_telegram_bot.TelegramBotTestCase._private_update
    _callback_update = test_telegram_bot.TelegramBotTestCase._callback_update
    tearDown = test_telegram_bot.TelegramBotTestCase.tearDown

    def setUp(self):
        test_telegram_bot.TelegramBotTestCase.setUp(self)
        self.update_id = 1000

    def send(self, text=None, *, callback=None, update=None):
        self.update_id += 1
        if update is None:
            update = (
                self._callback_update(callback, update_id=self.update_id)
                if callback
                else self._private_update(text)
            )
            update['update_id'] = self.update_id
        with patch(
            'app.routes.telegram_bot.send_telegram_message', return_value=True
        ) as sender, patch(
            'app.routes.telegram_bot.answer_telegram_callback_query', return_value=True
        ):
            response = self.client.post(
                '/telegram/webhook',
                json=update,
                headers={'X-Telegram-Bot-Api-Secret-Token': 'test-secret'},
            )
        self.assertEqual(response.status_code, 200)
        return sender.call_args

    def test_idea_and_bug_are_confirmed_and_visible_in_admin_panel(self):
        for button, kind, body in (
            ('Предложить идею', 'idea', 'Добавьте семейный бюджет'),
            ('Сообщить об ошибке', 'bug', 'Расход 850 рублей не появился в списке'),
        ):
            with self.subTest(kind=kind):
                prompt = self.send(button)
                self.assertTrue(prompt.args[1])
                preview = self.send(body)
                self.assertIn(body, preview.args[1])
                callback = preview.kwargs['reply_markup']['inline_keyboard'][0][0][
                    'callback_data'
                ]
                with self.app.app_context():
                    self.assertEqual(
                        FeedbackReport.query.filter_by(kind=kind).count(), 0
                    )
                    self.assertEqual(Expense.query.count(), 0)
                reply = self.send(callback=callback)
                self.assertIn('передано администратору', reply.args[1])
                self.send(callback=callback)
                with self.app.app_context():
                    report = FeedbackReport.query.filter_by(kind=kind).one()
                    self.assertEqual(report.user_id, self.user_id)
                    self.assertEqual(report.body, body)
                    self.assertEqual(report.status, 'new')
                    self.assertEqual(TelegramConversationState.query.count(), 0)
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.role = 'admin'
            db.session.commit()
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True
        html = self.client.get('/admin/feedback').get_data(as_text=True)
        self.assertIn('Добавьте семейный бюджет', html)
        self.assertIn('Расход 850 рублей не появился', html)

    def test_quick_command_preserves_message_text_and_replay_is_ignored(self):
        preview = self.send('/idea Хотелось бы фильтр "за год"')
        self.assertIn('Хотелось бы фильтр "за год"', preview.args[1])
        callback = preview.kwargs['reply_markup']['inline_keyboard'][0][0][
            'callback_data'
        ]
        update = self._callback_update(callback, update_id=2000)
        self.send(update=update)
        self.assertIsNone(self.send(update=update))
        with self.app.app_context():
            self.assertEqual(FeedbackReport.query.count(), 1)

    def test_cancel_does_not_submit_and_stale_button_cannot_send_another_draft(self):
        self.send('Предложить идею')
        preview = self.send('Первая идея')
        old_callback = preview.kwargs['reply_markup']['inline_keyboard'][0][0][
            'callback_data'
        ]
        self.send(callback='tg:cancel')
        self.send('/bug')
        self.send('Вторая ошибка')
        reply = self.send(callback=old_callback)
        self.assertIn('устарел', reply.args[1])
        with self.app.app_context():
            self.assertEqual(FeedbackReport.query.count(), 0)
            self.assertEqual(TelegramConversationState.query.one().step, 'confirm')

    def test_maximum_length_and_expiration(self):
        self.send('/bug')
        reply = self.send('x' * 4001)
        self.assertIn('4000', reply.args[1])
        preview = self.send('x' * 4000)
        self.assertLessEqual(len(preview.args[1]), 4096)
        callback = preview.kwargs['reply_markup']['inline_keyboard'][0][0][
            'callback_data'
        ]
        self.assertLessEqual(len(callback.encode()), 64)
        with self.app.app_context():
            TelegramConversationState.query.one().expires_at = utc_now() - timedelta(
                minutes=1
            )
            db.session.commit()
        self.assertIn('устарел', self.send(callback=callback).args[1])
        with self.app.app_context():
            self.assertEqual(FeedbackReport.query.count(), 0)

    def test_disabled_and_blocked_senders_cannot_submit(self):
        self.app.config['TELEGRAM_BOT_ENABLED'] = False
        self.assertIsNone(self.send('/idea Текст'))
        self.app.config['TELEGRAM_BOT_ENABLED'] = True
        with self.app.app_context():
            db.session.get(User, self.user_id).is_blocked = True
            db.session.commit()
        self.assertIn('заблокирован', self.send('/idea Текст').args[1])
        with self.app.app_context():
            self.assertEqual(FeedbackReport.query.count(), 0)
            self.assertEqual(TelegramConversationState.query.count(), 0)

    def test_another_sender_cannot_confirm_someone_elses_report(self):
        preview = self.send('/idea Добавьте отчёт за год')
        callback = preview.kwargs['reply_markup']['inline_keyboard'][0][0][
            'callback_data'
        ]
        with self.app.app_context():
            db.session.add(User(telegram_id=222222, username='another_sender'))
            db.session.commit()
        update = self._callback_update(
            callback, update_id=3000, telegram_id=222222, chat_id=222222
        )
        self.assertIn('устарел', self.send(update=update).args[1])
        with self.app.app_context():
            self.assertEqual(FeedbackReport.query.count(), 0)
            state = TelegramConversationState.query.one()
            self.assertEqual(state.telegram_id, 123456)
            self.assertEqual(state.step, 'confirm')
