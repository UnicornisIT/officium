import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app
from app.models import Debt, Expense, Income, Payment, TelegramConversationState, User
from app.services.telegram_bot_service import (
    build_debt_reminder_message,
    process_telegram_text,
)
from extensions import db


class TelegramBotTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'TELEGRAM_BOT_ENABLED': True,
            'TELEGRAM_WEBHOOK_SECRET': 'test-secret',
            'TELEGRAM_BOT_TOKEN': '123:test',
            'TELEGRAM_BOT_RATE_LIMIT_PER_MINUTE': 0,
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            user = User(
                telegram_id=123456,
                username='telegram_user',
                first_name='Telegram',
                role='user',
            )
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _private_update(self, text, telegram_id=123456, chat_id=123456):
        return {
            'update_id': 1,
            'message': {
                'message_id': 10,
                'date': 1785326400,
                'chat': {'id': chat_id, 'type': 'private'},
                'from': {'id': telegram_id, 'is_bot': False},
                'text': text,
            },
        }

    def _callback_update(self, data, update_id=2, telegram_id=123456, chat_id=123456):
        return {
            'update_id': update_id,
            'callback_query': {
                'id': f'callback-{update_id}',
                'from': {'id': telegram_id, 'is_bot': False},
                'message': {
                    'message_id': 10,
                    'date': 1785326400,
                    'chat': {'id': chat_id, 'type': 'private'},
                },
                'data': data,
            },
        }

    def test_webhook_rejects_requests_without_secret(self):
        response = self.client.post('/telegram/webhook', json=self._private_update('итог'))

        self.assertEqual(response.status_code, 403)

    def test_webhook_rejects_requests_with_wrong_secret(self):
        response = self.client.post(
            '/telegram/webhook',
            json=self._private_update('итог'),
            headers={'X-Telegram-Bot-Api-Secret-Token': 'wrong'},
        )

        self.assertEqual(response.status_code, 403)

    def test_webhook_ignores_unknown_telegram_user(self):
        with patch('app.routes.telegram_bot.send_telegram_message') as send_message:
            response = self.client.post(
                '/telegram/webhook',
                json=self._private_update('расход 850 продукты пятерочка', telegram_id=999999),
                headers={'X-Telegram-Bot-Api-Secret-Token': 'test-secret'},
            )

        self.assertEqual(response.status_code, 200)
        send_message.assert_called_once()
        self.assertIn('не нашел ваш аккаунт', send_message.call_args.args[1])
        with self.app.app_context():
            self.assertEqual(Expense.query.count(), 0)

    def test_webhook_rejects_group_chat_for_privacy(self):
        update = self._private_update('итог')
        update['message']['chat'] = {'id': -100, 'type': 'group'}

        with patch('app.routes.telegram_bot.send_telegram_message') as send_message:
            response = self.client.post(
                '/telegram/webhook',
                json=update,
                headers={'X-Telegram-Bot-Api-Secret-Token': 'test-secret'},
            )

        self.assertEqual(response.status_code, 200)
        send_message.assert_called_once()
        self.assertIn('только в личном чате', send_message.call_args.args[1])

    def test_webhook_processes_same_update_only_once(self):
        update = self._private_update('расход 850 продукты пятерочка')
        headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-secret'}

        with patch('app.routes.telegram_bot.send_telegram_message') as send_message:
            first_response = self.client.post('/telegram/webhook', json=update, headers=headers)
            second_response = self.client.post('/telegram/webhook', json=update, headers=headers)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        send_message.assert_called_once()
        with self.app.app_context():
            self.assertEqual(Expense.query.count(), 1)

    def test_stepwise_expense_flow_uses_buttons_and_confirmation(self):
        headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-secret'}

        def message_update(text, update_id):
            update = self._private_update(text)
            update['update_id'] = update_id
            return update

        with patch('app.routes.telegram_bot.send_telegram_message') as send_message, \
                patch('app.routes.telegram_bot.answer_telegram_callback_query') as answer_callback:
            self.client.post('/telegram/webhook', json=message_update('Расход', 100), headers=headers)
            self.client.post('/telegram/webhook', json=message_update('850', 101), headers=headers)
            self.client.post('/telegram/webhook', json=self._callback_update('tg:expense_category:products', update_id=102), headers=headers)
            self.client.post('/telegram/webhook', json=message_update('пятерочка', 103), headers=headers)
            self.client.post('/telegram/webhook', json=self._callback_update('tg:date:today', update_id=104), headers=headers)

            with self.app.app_context():
                self.assertEqual(Expense.query.count(), 0)
                state = TelegramConversationState.query.filter_by(telegram_id=123456).one()
                self.assertEqual(state.flow, 'expense')
                self.assertEqual(state.step, 'confirm')
                self.assertIn('amount', state.data)
                self.assertNotIn('Расход 850 продукты пятерочка', state.data)

            self.client.post('/telegram/webhook', json=self._callback_update('tg:confirm', update_id=105), headers=headers)

        self.assertGreaterEqual(send_message.call_count, 6)
        self.assertGreaterEqual(answer_callback.call_count, 3)
        with self.app.app_context():
            expense = Expense.query.filter_by(user_id=self.user_id).one()
            self.assertEqual(expense.amount, Decimal('850.00'))
            self.assertEqual(expense.category, 'products')
            self.assertEqual(expense.title, 'пятерочка')
            self.assertEqual(TelegramConversationState.query.count(), 0)

    def test_can_create_expense_from_telegram_text(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            reply = process_telegram_text(user, 'расход 850 продукты пятерочка')

            expense = Expense.query.filter_by(user_id=self.user_id).one()

        self.assertIn('Записал расход', reply)
        self.assertEqual(expense.amount, Decimal('850.00'))
        self.assertEqual(expense.category, 'products')
        self.assertEqual(expense.title, 'пятерочка')
        self.assertEqual(expense.payment_method, 'card')

    def test_can_create_income_from_telegram_text(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            reply = process_telegram_text(user, 'доход 120000 зарплата работа')

            income = Income.query.filter_by(user_id=self.user_id).one()

        self.assertIn('Записал доход', reply)
        self.assertEqual(income.amount, Decimal('120000.00'))
        self.assertEqual(income.category, 'salary')
        self.assertEqual(income.source, 'работа')

    def test_can_create_debt_without_defaulting_due_date_to_today(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            reply = process_telegram_text(user, 'долг 300000 сбер кредит мин=15000 ставка=18.5')

            debt = Debt.query.filter_by(user_id=self.user_id).one()

        self.assertIn('Добавил долг', reply)
        self.assertEqual(debt.remaining_amount, Decimal('300000.00'))
        self.assertEqual(debt.minimum_payment, Decimal('15000.00'))
        self.assertEqual(debt.interest_rate, Decimal('18.50'))
        self.assertIsNone(debt.next_payment_date)
        self.assertFalse(debt.is_payment_recurring)

    def test_can_create_payment_for_matching_debt(self):
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Sber',
                debt_type='consumer_credit',
                product_name='Cash loan',
                total_amount=Decimal('100000.00'),
                remaining_amount=Decimal('100000.00'),
                minimum_payment=Decimal('5000.00'),
                next_payment_date=date(2026, 8, 15),
                is_payment_recurring=True,
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            user = db.session.get(User, self.user_id)

            reply = process_telegram_text(user, 'платеж 5000 sber дата=2026-08-15')
            payment = Payment.query.filter_by(debt_id=debt.id).one()

        self.assertIn('Записал платеж', reply)
        self.assertEqual(payment.amount, Decimal('5000.00'))
        self.assertEqual(payment.payment_date, date(2026, 8, 15))
        self.assertEqual(payment.remaining_after_payment, Decimal('95000.00'))

    def test_debt_reminder_contains_upcoming_and_overdue_debts(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            db.session.add_all([
                Debt(
                    user_id=self.user_id,
                    bank_name='Sber',
                    debt_type='consumer_credit',
                    product_name='Cash loan',
                    total_amount=Decimal('100000.00'),
                    remaining_amount=Decimal('90000.00'),
                    minimum_payment=Decimal('5000.00'),
                    next_payment_date=date(2026, 7, 30),
                    status='active',
                ),
                Debt(
                    user_id=self.user_id,
                    bank_name='Tinkoff',
                    debt_type='credit_card',
                    product_name='Card',
                    total_amount=Decimal('50000.00'),
                    remaining_amount=Decimal('10000.00'),
                    minimum_payment=Decimal('3000.00'),
                    next_payment_date=date(2026, 7, 27),
                    status='active',
                ),
            ])
            db.session.commit()

            message = build_debt_reminder_message(user, today=date(2026, 7, 29), days=3)

        self.assertIn('Sber Cash loan', message)
        self.assertIn('через 1 дн.', message)
        self.assertIn('Tinkoff Card', message)
        self.assertIn('просрочен на 2 дн.', message)


if __name__ == '__main__':
    unittest.main()
