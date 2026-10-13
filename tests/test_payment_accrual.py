"""Payments through the API use the accrual engine. Amounts are worked out by
hand in comments: interest = balance * 18% * days / 365, penalty = overdue
amount * 20% * days / 365, both rounded half up to kopecks."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app
from app.models import Debt, Payment, User
from app.services.debt_schedule_service import build_debt_payment_schedule
from extensions import db


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 26)


class PaymentAccrualApiTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'payment-accrual-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        self.date_patch = patch('app.services.payment_service.date', FrozenDate)
        self.date_patch.start()

        with self.app.app_context():
            db.create_all()
            owner = User(telegram_id=1101, username='payer', first_name='Payer', role='user')
            stranger = User(telegram_id=1102, username='stranger', first_name='Stranger', role='user')
            db.session.add_all([owner, stranger])
            db.session.commit()
            self.user_id = owner.id
            self.stranger_id = stranger.id
            debt = Debt(user_id=owner.id, status='active', **self.debt_values())
            db.session.add(debt)
            db.session.commit()
            self.debt_id = debt.id

    def tearDown(self):
        self.date_patch.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    @staticmethod
    def debt_values(**overrides):
        values = {
            'bank_name': 'Банк',
            'debt_type': 'consumer_credit',
            'product_name': 'Кредит',
            'total_amount': Decimal('100000.00'),
            'remaining_amount': Decimal('100000.00'),
            'minimum_payment': Decimal('5000.00'),
            'interest_rate': Decimal('18.00'),
            'next_payment_date': date(2026, 8, 15),
            'accrual_start_date': date(2026, 7, 15),
            'is_payment_recurring': True,
            'day_count_convention': 'actual_year',
            'penalty_rate': Decimal('20.00'),
        }
        values.update(overrides)
        return values

    def login(self, user_id=None):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id or self.user_id)
            session['_fresh'] = True

    def post_payment(self, **body):
        response = self.client.post(f'/api/debts/{self.debt_id}/payments', json=body)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def stored(self, payment_id):
        with self.app.app_context():
            payment = db.session.get(Payment, payment_id)
            return {
                'principal': payment.principal_amount,
                'interest': payment.interest_amount,
                'penalty': payment.penalty_amount,
                'source': payment.breakdown_source,
            }

    def debt(self):
        with self.app.app_context():
            debt = db.session.get(Debt, self.debt_id)
            return debt.remaining_amount, debt.next_payment_date

    def test_on_time_payment_matches_the_schedule(self):
        self.login()

        payload = self.post_payment(amount='5000', payment_date='2026-08-15')

        # 15.07-15.08 = 31 days: 100000 * 18% * 31 / 365 = 1528.77, principal 3471.23
        self.assertEqual(payload['payment']['interest_amount'], 1528.77)
        self.assertEqual(payload['payment']['principal_amount'], 3471.23)
        self.assertEqual(payload['payment']['penalty_amount'], 0.0)
        self.assertEqual(payload['payment']['breakdown_source'], 'auto')
        self.assertTrue(payload['next_payment_date_advanced'])
        self.assertEqual(self.debt(), (Decimal('96528.77'), date(2026, 9, 15)))

        schedule_debt = Debt(**self.debt_values(interest_period_start_date=date(2026, 7, 15)))
        first_row = build_debt_payment_schedule(schedule_debt, today=date(2026, 7, 20))['rows'][0]
        self.assertEqual(first_row['remaining'], 96528.77)

    def test_late_payment_then_penalty_paid_in_legal_order(self):
        self.login()

        late = self.post_payment(amount='5000', payment_date='2026-08-25')
        extra = self.post_payment(amount='100', payment_date='2026-08-26')

        # 15.08: 5000 overdue = 1528.77 interest + 3471.23 principal; the late
        # payment covers exactly that. Penalty 10 days: 5000 * 20% * 10 / 365 = 27.40,
        # paid first by the next payment; the rest 72.60 goes to current interest
        # (493.15 for 15.08-25.08 on 100000 plus 47.60 for 25.08-26.08 on 96528.77).
        self.assertEqual(self.stored(late['payment']['id']), {
            'principal': Decimal('3471.23'), 'interest': Decimal('1528.77'),
            'penalty': Decimal('0.00'), 'source': 'auto',
        })
        self.assertEqual(self.stored(extra['payment']['id']), {
            'principal': Decimal('0.00'), 'interest': Decimal('72.60'),
            'penalty': Decimal('27.40'), 'source': 'auto',
        })
        self.assertEqual(self.debt(), (Decimal('96528.77'), date(2026, 9, 15)))

    def test_editing_and_deleting_a_past_payment_recalculates_later_ones(self):
        self.login()
        late = self.post_payment(amount='5000', payment_date='2026-08-25')
        extra = self.post_payment(amount='100', payment_date='2026-08-26')

        response = self.client.put(f'/api/debts/{self.debt_id}/payments/{late["payment"]["id"]}', json={
            'amount': '5000',
            'payment_date': '2026-08-15',
        })
        self.assertEqual(response.status_code, 200, response.get_json())

        # Paid on time now: no penalty; 100 goes to interest accrued since 15.08:
        # 96528.77 * 18% * 11 / 365 = 523.64.
        self.assertEqual(self.stored(extra['payment']['id'])['penalty'], Decimal('0.00'))
        self.assertEqual(self.stored(extra['payment']['id'])['interest'], Decimal('100.00'))

        response = self.client.delete(f'/api/debts/{self.debt_id}/payments/{late["payment"]["id"]}')
        self.assertEqual(response.status_code, 200, response.get_json())

        # Without the 5000 the 15.08 installment is overdue again: the 100 now
        # pays overdue interest, the principal is back at 100000 and the next
        # payment date returns to the unpaid 15.08.
        self.assertEqual(self.stored(extra['payment']['id']), {
            'principal': Decimal('0.00'), 'interest': Decimal('100.00'),
            'penalty': Decimal('0.00'), 'source': 'auto',
        })
        self.assertEqual(self.debt(), (Decimal('100000.00'), date(2026, 8, 15)))
        payload = response.get_json()
        self.assertEqual(payload['debt']['remaining_amount'], 100000.0)

    def test_manual_breakdown_has_priority_and_can_be_switched_off(self):
        self.login()
        manual = self.post_payment(amount='5000', payment_date='2026-08-15', principal_amount='4000')
        payment_id = manual['payment']['id']

        self.assertEqual(self.stored(payment_id)['principal'], Decimal('4000.00'))
        self.assertEqual(self.stored(payment_id)['source'], 'manual')
        self.assertEqual(self.debt()[0], Decimal('96000.00'))

        # The edit form sends the old breakdown back; the switch decides.
        response = self.client.put(f'/api/debts/{self.debt_id}/payments/{payment_id}', json={
            'amount': '5000',
            'payment_date': '2026-08-15',
            'principal_amount': '4000.00',
            'interest_amount': '1000.00',
            'manual_breakdown': False,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.stored(payment_id)['principal'], Decimal('3471.23'))
        self.assertEqual(self.stored(payment_id)['source'], 'auto')

        response = self.client.put(f'/api/debts/{self.debt_id}/payments/{payment_id}', json={
            'amount': '5000',
            'payment_date': '2026-08-15',
            'principal_amount': '4500',
            'manual_breakdown': True,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.stored(payment_id)['principal'], Decimal('4500.00'))
        self.assertEqual(self.debt()[0], Decimal('95500.00'))

    def test_manual_breakdown_must_name_principal_or_interest(self):
        self.login()

        response = self.client.post(f'/api/debts/{self.debt_id}/payments', json={
            'amount': '5000',
            'payment_date': '2026-08-15',
            'manual_breakdown': True,
        })

        self.assertEqual(response.status_code, 422)
        with self.app.app_context():
            self.assertEqual(Payment.query.count(), 0)

    def test_payment_cannot_be_deleted_through_another_users_debt(self):
        self.login()
        payment_id = self.post_payment(amount='5000', payment_date='2026-08-15')['payment']['id']

        self.login(self.stranger_id)
        response = self.client.delete(f'/api/debts/{self.debt_id}/payments/{payment_id}')

        self.assertEqual(response.status_code, 404)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Payment, payment_id))

    def test_overpayment_is_rejected_without_saving(self):
        self.login()

        # Total on 15.08 is 100000 + 1528.77 interest.
        response = self.client.post(f'/api/debts/{self.debt_id}/payments', json={
            'amount': '101528.78',
            'payment_date': '2026-08-15',
        })

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.debt(), (Decimal('100000.00'), date(2026, 8, 15)))
        with self.app.app_context():
            self.assertEqual(Payment.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
