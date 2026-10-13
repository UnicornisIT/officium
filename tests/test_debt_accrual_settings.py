"""Debt form settings for accruals: penalty rate, capitalization, grace
period, manual date moves and accepting the bank's balance.

Base debt: 100000 at 18% (actual/365), 5000 due monthly on the 15th,
accruals since 15.07, today 25.08."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app
from app.models import Debt, Payment, User
from extensions import db


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 25)


class DebtAccrualSettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'debt-accrual-settings-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        self.patches = [
            patch(f'{module}.date', FrozenDate)
            for module in ('app.models', 'app.services.payment_service')
        ]
        for date_patch in self.patches:
            date_patch.start()
        with self.app.app_context():
            db.create_all()
            user = User(telegram_id=1401, username='settings', first_name='Settings', role='user')
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

    def tearDown(self):
        for date_patch in self.patches:
            date_patch.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def create(self, **overrides):
        body = {
            'bank_name': 'Банк',
            'debt_type': 'consumer_credit',
            'product_name': 'Кредит',
            'total_amount': '100000',
            'remaining_amount': '100000',
            'minimum_payment': '5000',
            'interest_rate': '18',
            'next_payment_date': '2026-08-15',
            'is_payment_recurring': True,
        }
        body.update(overrides)
        return self.client.post('/api/debts', json=body)

    def debt_id(self, **overrides):
        response = self.create(**overrides)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()['debt']['id']

    def stored(self, debt_id):
        with self.app.app_context():
            return db.session.get(Debt, debt_id)

    def test_new_debts_get_the_default_penalty_rate_except_split(self):
        loan = self.create().get_json()['debt']
        split = self.create(debt_type='split', interest_rate='', minimum_payment='2500').get_json()['debt']
        cleared = self.create(penalty_rate=None).get_json()['debt']

        self.assertEqual(loan['penalty_rate'], 20.0)
        self.assertIsNone(split['penalty_rate'])
        self.assertIsNone(cleared['penalty_rate'])
        # Accruals start at the cycle before the first due date.
        self.assertEqual(loan['accrual_start_date'], '2026-07-15')

    def test_penalty_rate_is_validated(self):
        for value in ('-1', '100.01'):
            response = self.create(penalty_rate=value)
            self.assertEqual(response.status_code, 422, value)
            self.assertIn('Ставка неустойки', response.get_json()['error'])

    def test_grace_period_belongs_to_cards_only(self):
        card = self.create(
            debt_type='credit_card', interest_period_start_date='2026-07-20',
            grace_period_end_date='2026-10-20',
        ).get_json()['debt']
        loan = self.create(grace_period_end_date='2026-10-20').get_json()['debt']
        bad = self.create(
            debt_type='credit_card', interest_period_start_date='2026-07-20',
            grace_period_end_date='2026-07-20',
        )

        self.assertEqual(card['grace_period_end_date'], '2026-10-20')
        self.assertIsNone(loan['grace_period_end_date'])
        self.assertEqual(bad.status_code, 422)
        self.assertIn('льготного периода', bad.get_json()['error'])

    def test_new_card_in_grace_period_is_not_overdue(self):
        # The grace period began on 01.07, the next payment is 10.09: earlier
        # installments are settled, tracking starts at the cycle start 10.08.
        debt = self.create(
            debt_type='credit_card', remaining_amount='40000', minimum_payment='3000',
            interest_rate='30', next_payment_date='2026-09-10',
            interest_period_start_date='2026-07-01', grace_period_end_date='2026-10-31',
        ).get_json()['debt']

        self.assertEqual(debt['accrual_start_date'], '2026-08-10')
        self.assertFalse(debt['is_overdue'])
        self.assertEqual(debt['effective_next_payment_date'], '2026-09-10')
        self.assertEqual(debt['accrued_interest'], 0.0)
        with self.app.app_context():
            state = db.session.get(Debt, debt['id']).accrual_state(date(2026, 8, 25))
        # Grace interest from 01.07 is pending: 40000 * 30% * 55 / 365 = 1808.22
        self.assertEqual(state['grace_interest_pending'], Decimal('1808.22'))

    def test_split_cannot_capitalize_interest(self):
        split = self.create(debt_type='split', capitalize_interest=True).get_json()['debt']
        loan = self.create(capitalize_interest=True).get_json()['debt']

        self.assertFalse(split['capitalize_interest'])
        self.assertTrue(loan['capitalize_interest'])

    def test_moving_the_payment_date_forward_closes_the_skipped_cycle(self):
        debt_id = self.debt_id()
        self.assertTrue(self.client.get(f'/api/debts/{debt_id}').get_json()['debt']['is_overdue'])

        response = self.client.put(f'/api/debts/{debt_id}', json={'next_payment_date': '2026-09-15'})

        self.assertEqual(response.status_code, 200, response.get_json())
        debt = response.get_json()['debt']
        self.assertEqual(debt['accrual_start_date'], '2026-08-15')
        self.assertFalse(debt['is_overdue'])
        # Interest since the new start: 100000 * 18% * 10 / 365 = 493.15
        self.assertEqual(debt['total_debt'], 100493.15)

        response = self.client.put(f'/api/debts/{debt_id}', json={'next_payment_date': '2026-08-15'})
        # Moving it back does not reopen the closed history.
        self.assertEqual(response.get_json()['debt']['accrual_start_date'], '2026-08-15')

    def test_later_interest_period_start_moves_the_accrual_start(self):
        debt_id = self.debt_id(
            remaining_amount='500000', total_amount='500000', interest_rate='30.9',
            minimum_payment='16454.19', first_payment_amount='2539.73',
            next_payment_date='2026-08-28', day_count_convention='fixed_365',
        )
        self.assertEqual(self.stored(debt_id).accrual_start_date, date(2026, 7, 28))

        response = self.client.put(f'/api/debts/{debt_id}', json={'interest_period_start_date': '2026-08-22'})

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.stored(debt_id).accrual_start_date, date(2026, 8, 22))
        # The loan was issued on 22.08: 3 days to 25.08 at 30.9% / 365:
        # 500000 * 30.9% * 3 / 365 = 1269.86, nothing is overdue yet.
        debt = response.get_json()['debt']
        self.assertFalse(debt['is_overdue'])
        self.assertEqual(debt['accrued_interest'], 1269.86)

    def test_changing_the_rate_resplits_recorded_payments(self):
        debt_id = self.debt_id()
        payment = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '5000', 'payment_date': '2026-08-15',
        }).get_json()['payment']
        self.assertEqual(payment['interest_amount'], 1528.77)

        response = self.client.put(f'/api/debts/{debt_id}', json={'interest_rate': '24'})

        self.assertEqual(response.status_code, 200, response.get_json())
        with self.app.app_context():
            stored = db.session.get(Payment, payment['id'])
            # 100000 * 24% * 31 / 365 = 2038.36, principal 2961.64
            self.assertEqual(stored.interest_amount, Decimal('2038.36'))
            self.assertEqual(stored.principal_amount, Decimal('2961.64'))
        self.assertEqual(self.stored(debt_id).remaining_amount, Decimal('97038.36'))

    def test_accepting_the_bank_balance_makes_a_manual_split(self):
        debt_id = self.debt_id()
        payment = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '5000', 'payment_date': '2026-08-15', 'bank_remaining_after_payment': '96500.00',
        }).get_json()['payment']
        self.assertEqual(payment['remaining_after_payment'], 96528.77)

        response = self.client.post(f'/api/debts/{debt_id}/payments/{payment["id"]}/accept-bank-remaining')

        self.assertEqual(response.status_code, 200, response.get_json())
        accepted = response.get_json()['payment']
        # 28.77 more goes to principal: 3471.23 + 28.77 = 3500.00, interest 1500.00
        self.assertEqual(accepted['principal_amount'], 3500.0)
        self.assertEqual(accepted['interest_amount'], 1500.0)
        self.assertEqual(accepted['breakdown_source'], 'manual')
        self.assertEqual(accepted['remaining_after_payment'], 96500.0)
        self.assertEqual(self.stored(debt_id).remaining_amount, Decimal('96500.00'))

    def test_bank_balance_that_the_payment_cannot_explain_is_rejected(self):
        debt_id = self.debt_id()
        payment = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '5000', 'payment_date': '2026-08-15', 'bank_remaining_after_payment': '90000.00',
        }).get_json()['payment']

        response = self.client.post(f'/api/debts/{debt_id}/payments/{payment["id"]}/accept-bank-remaining')

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.stored(debt_id).remaining_amount, Decimal('96528.77'))


if __name__ == '__main__':
    unittest.main()
