import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BrokenBarrierError
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from app import create_app
from app.models import AppSetting, Debt, TelegramConversationState, User
from app.services import telegram_bot_service as bot
from app.services.debt_accrual_service import accrual_start_date
from extensions import db


class DebtLimitConcurrencyTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                'TESTING': True,
                'SECRET_KEY': 'debt-limit-test-secret',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + Path(self.directory.name, 'debts.db').as_posix(),
                'SQLALCHEMY_ENGINE_OPTIONS': {'connect_args': {'timeout': 15}},
                'WTF_CSRF_ENABLED': False,
            }
        )
        self.payload = {
            'bank_name': 'Bank',
            'product_name': 'Loan',
            'debt_type': 'consumer_credit',
            'total_amount': '1000',
            'remaining_amount': '1000',
            'amount': '1000',
        }
        with self.app.app_context():
            db.create_all()
            user = User(telegram_id=19701, username='debt-limit-owner', role='user')
            db.session.add_all([user, AppSetting(key='debt_limit_per_user', value='1')])
            db.session.commit()
            self.user_id = user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.directory.cleanup()

    def create_api(self):
        with self.app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = str(self.user_id)
                session['_fresh'] = True
            response = client.post('/api/debts', json=self.payload)
            self.assertIn(response.status_code, (201, 422), response.get_json())
            return response.status_code == 201

    def create_bot(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            state = TelegramConversationState(flow='debt', data=json.dumps(self.payload))
            try:
                result = bot._finish_conversation(user, user.telegram_id, state)
                self.assertIn('Добавил долг', result.reply_text)
                return True
            except ValueError as exc:
                db.session.rollback()
                self.assertIn('Достигнут лимит долгов: 1', str(exc))
                return False

    def run_concurrent(self, channels, expected_successes):
        start = Barrier(len(channels))
        checked = Barrier(len(channels))

        def delayed_accrual(debt):
            # Force unprotected creators past the quota check before any insert.
            try:
                checked.wait(timeout=1)
            except BrokenBarrierError:
                pass
            return accrual_start_date(debt)

        def create(channel):
            start.wait(timeout=10)
            return self.create_api() if channel == 'api' else self.create_bot()

        with (
            patch('app.routes.debts.accrual_start_date', side_effect=delayed_accrual),
            patch.object(
                bot,
                'accrual_start_date',
                side_effect=delayed_accrual,
            ),
        ):
            with ThreadPoolExecutor(max_workers=len(channels)) as executor:
                results = list(executor.map(create, channels))
        self.assertEqual(sum(results), expected_successes)
        with self.app.app_context():
            self.assertEqual(Debt.query.filter_by(user_id=self.user_id).count(), 1)

    def test_api_competes_for_last_slot(self):
        self.run_concurrent(['api'] * 5, 1)

    def test_bot_competes_for_last_slot(self):
        self.run_concurrent(['bot'] * 5, 1)

    def test_api_and_bot_share_limit(self):
        self.run_concurrent(['api', 'bot', 'api', 'bot', 'api'], 1)

    def test_full_limit_rejects_all_requests(self):
        self.assertTrue(self.create_api())
        self.run_concurrent(['api'] * 5, 0)

    def test_api_integrity_error_rolls_back(self):
        with self.app.app_context(), self.app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = str(self.user_id)
                session['_fresh'] = True
            with patch.object(db.session, 'commit', side_effect=IntegrityError('insert', {}, Exception('failed'))):
                response = client.post('/api/debts', json=self.payload)
            self.assertEqual(response.status_code, 500)
            self.assertFalse(db.session().in_transaction())
            self.assertEqual(Debt.query.count(), 0)
            response = client.post('/api/debts', json=self.payload)
            self.assertEqual(response.status_code, 201)

    def test_api_validation_error_releases_lock(self):
        with self.app.app_context(), self.app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = str(self.user_id)
                session['_fresh'] = True
            response = client.post('/api/debts', json={**self.payload, 'accrual_start_date': 'invalid'})
            self.assertEqual(response.status_code, 422)
            self.assertFalse(db.session().in_transaction())
            self.assertEqual(Debt.query.count(), 0)

    def test_bot_integrity_error_preserves_conversation_for_retry(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            bot.process_telegram_text(user, 'долг 1000 банк кредит')
            db.session.commit()
            with patch.object(db.session, 'commit', side_effect=IntegrityError('insert', {}, Exception('failed'))):
                result = bot.process_telegram_callback_result(user, user.telegram_id, 'tg:confirm')
            self.assertIn('Не получилось', result.reply_text)
            self.assertEqual(Debt.query.count(), 0)
            self.assertEqual(TelegramConversationState.query.count(), 1)
            result = bot.process_telegram_callback_result(user, user.telegram_id, 'tg:confirm')
            self.assertIn('Добавил долг', result.reply_text)
            self.assertEqual(Debt.query.count(), 1)
            self.assertEqual(TelegramConversationState.query.count(), 0)
