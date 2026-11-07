"""The payment schedule starts from the debt's current state.

Base debt: 100000 at 18% (actual/365), 5000 due monthly on the 15th,
accruals since 15.07, penalty 20% a year."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

from app import create_app
from app.models import Debt, Payment, SplitPurchase, User
from app.services.debt_accrual_service import get_debt_state
from app.services.debt_schedule_service import build_debt_payment_schedule
from app.services.financial_plan_service import build_financial_plan
from app.services.payment_service import add_payment
from extensions import db


class ScheduleAccrualTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'schedule-accrual-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        user = User(telegram_id=1501, username='schedule', first_name='Schedule', role='user')
        db.session.add(user)
        db.session.commit()
        self.user_id = user.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def debt(self, **overrides):
        values = {
            'user_id': self.user_id, 'status': 'active', 'bank_name': 'Банк',
            'debt_type': 'consumer_credit', 'product_name': 'Кредит',
            'total_amount': Decimal('100000.00'), 'remaining_amount': Decimal('100000.00'),
            'minimum_payment': Decimal('5000.00'), 'interest_rate': Decimal('18.00'),
            'next_payment_date': date(2026, 8, 15), 'accrual_start_date': date(2026, 7, 15),
            'is_payment_recurring': True, 'day_count_convention': 'actual_year',
            'penalty_rate': Decimal('20.00'),
        }
        values.update(overrides)
        debt = Debt(**values)
        db.session.add(debt)
        db.session.commit()
        return debt

    def pay(self, debt, payment_date, amount, principal):
        db.session.add(Payment(
            debt_id=debt.id, amount=Decimal(amount), principal_amount=Decimal(principal),
            interest_amount=Decimal(amount) - Decimal(principal), fee_amount=Decimal('0'),
            payment_date=payment_date, remaining_after_payment=Decimal('0'),
        ))
        debt.remaining_amount = Decimal(str(debt.remaining_amount)) - Decimal(principal)
        db.session.commit()

    def stale_debt(self, paid=True, **overrides):
        values = {
            'total_amount': Decimal('500000.00'),
            'remaining_amount': Decimal('500000.00'),
            'minimum_payment': Decimal('25000.00'),
            'interest_rate': Decimal('25.00'),
            'next_payment_date': date(2025, 3, 25),
            'accrual_start_date': date(2025, 2, 25),
            'is_payment_recurring': False,
        }
        values.update(overrides)
        debt = self.debt(**values)
        if paid:
            for month in range(20):
                add_payment(
                    debt, Decimal('25000.00') + (debt.monthly_fee_amount or 0),
                    payment_date=date(2025, 3, 25) + relativedelta(months=month),
                )
        return debt

    def test_plan_counts_single_installment_when_stale_date_but_paid(self):
        today = date(2026, 11, 5)
        for fee in (Decimal('0'), Decimal('300')):
            with self.subTest(fee=fee):
                debt = self.stale_debt(monthly_fee_amount=fee)
                self.assertFalse(debt.accrual_state(today)['is_overdue'])

                plan = build_financial_plan(self.user_id, today=today)
                item = next(item for item in plan['debt_items'] if item['id'] == debt.id)

                self.assertEqual(plan['totals']['obligations'], float(Decimal('25000') + fee))
                self.assertEqual(item['monthly_amount'], float(Decimal('25000') + fee))
                self.assertEqual(item['scheduled_payments_count'], 1)
                self.assertEqual(item['interval_label'], 'обязательный платёж по графику')
                self.assertEqual(item['planned_payments'][0]['due_date'], date(2026, 11, 25))
                self.assertEqual(debt.next_payment_date, date(2025, 3, 25))
                debt.status = 'closed'
                db.session.commit()

    def test_monthly_plan_does_not_include_accumulated_arrears_or_penalties(self):
        today = date(2026, 11, 5)
        debt = self.stale_debt(paid=False)
        state = debt.accrual_state(today)
        rows = build_debt_payment_schedule(debt, today=today)['rows']

        plan = build_financial_plan(self.user_id, today=today)
        item = plan['debt_items'][0]

        self.assertTrue(item['is_overdue'])
        self.assertEqual(rows[0]['status'], 'overdue')
        self.assertEqual(rows[0]['payment'], float(state['amount_to_clear_overdue']))
        self.assertEqual(item['scheduled_payments_count'], 1)
        self.assertEqual(item['monthly_amount'], 25000.0)
        self.assertEqual(plan['totals']['obligations'], 25000.0)
        self.assertEqual(sum(payment['amount'] for payment in item['planned_payments']), 25000.0)
        self.assertEqual(item['overdue_amount'], float(state['overdue_amount']))
        self.assertEqual(item['penalty'], float(state['penalty']))
        self.assertGreater(item['projected_remaining_after_plan'], 0)

    def test_schedule_first_row_is_future_when_anchor_in_past(self):
        debt = self.stale_debt()
        today = date(2026, 11, 5)

        rows = build_debt_payment_schedule(debt, today=today)['rows']

        self.assertEqual(rows[0]['payment_date'], '2026-11-25')
        self.assertEqual(rows[0]['payment'], 25000.0)
        self.assertTrue(all(date.fromisoformat(row['payment_date']) >= today for row in rows))

    def test_schedule_fallback_uses_latest_payment_anchor_without_month_end_drift(self):
        debt = self.stale_debt()
        add_payment(debt, Decimal('25000'), payment_date=date(2026, 10, 31))
        # Migrated records can lack a contractual day until their next payment.
        debt.payment_day = None
        db.session.commit()
        today = date(2027, 3, 1)

        with patch('app.services.debt_schedule_service._accrual_state', return_value=None):
            rows = build_debt_payment_schedule(debt, today=today)['rows']

        self.assertEqual(rows[0]['payment_date'], '2027-03-31')
        self.assertEqual(debt.next_payment_date, date(2025, 3, 25))

    def test_schedule_fallback_prefers_explicit_contract_day(self):
        debt = self.stale_debt(payment_day=25)
        add_payment(debt, Decimal('25000'), payment_date=date(2026, 10, 31))

        with patch('app.services.debt_schedule_service._accrual_state', return_value=None):
            rows = build_debt_payment_schedule(debt, today=date(2027, 3, 1))['rows']

        self.assertEqual(rows[0]['payment_date'], '2027-03-25')

    def test_plan_ignores_past_rows_without_overdue_status(self):
        self.debt()
        rows = [
            {'payment_date': due, 'payment': amount, 'remaining': 90000.0, **status}
            for due, amount, status in (
                ('2026-07-15', 10000, {}),
                ('2026-07-20', 2000, {'status': 'overdue'}),
                ('2026-07-25', 3000, {'status': 'partly_paid'}),
                ('2026-08-01', 4000, {}),
                ('2026-08-15', 5000, {'status': 'paid'}),
                ('2026-09-15', 6000, {}),
            )
        ]
        with patch('app.services.financial_plan_service.build_debt_payment_schedule', return_value={'rows': rows}):
            plan = build_financial_plan(self.user_id, today=date(2026, 8, 1))

        item = plan['debt_items'][0]
        self.assertEqual(item['monthly_amount'], 2000.0)
        self.assertEqual(item['scheduled_payments_count'], 1)

    def test_monthly_plan_counts_one_installment_on_period_boundary(self):
        self.debt()
        rows = [
            {'payment_date': due, 'payment': 5000, 'remaining': 90000.0}
            for due in ('2026-08-01', '2026-09-01')
        ]
        with patch('app.services.financial_plan_service.build_debt_payment_schedule', return_value={'rows': rows}):
            plan = build_financial_plan(self.user_id, today=date(2026, 8, 1))

        self.assertEqual(plan['totals']['obligations'], 5000.0)
        self.assertEqual(plan['debt_items'][0]['scheduled_payments_count'], 1)

    def test_plan_warns_about_stale_date_when_schedule_unavailable(self):
        debt = self.stale_debt()
        today = date(2026, 11, 5)
        with patch('app.services.financial_plan_service.build_debt_payment_schedule', side_effect=ValueError):
            plan = build_financial_plan(self.user_id, today=today)

        item = plan['debt_items'][0]
        self.assertIsNone(item['planned_payments'][0]['due_date'])
        self.assertTrue(any(gap['kind'] == 'stale_date' for gap in plan['missing_data']))
        self.assertEqual(debt.next_payment_date, date(2025, 3, 25))

    def test_partial_payment_in_the_cycle_is_reflected_in_the_first_row(self):
        debt = self.debt()
        # 2000 on 05.08: 21 days of interest 1035.62, principal 964.38.
        self.pay(debt, date(2026, 8, 5), '2000', '964.38')

        rows = build_debt_payment_schedule(debt, today=date(2026, 8, 10))['rows']

        # The first row carries the engine's interest for 05.08-15.08 on
        # 99035.62: 99035.62 * 18% * 10 / 365 = 488.39.
        engine_interest = get_debt_state(debt, as_of=date(2026, 8, 15))['current_interest']
        self.assertEqual(engine_interest, Decimal('488.39'))
        # Only 5000 - 2000 = 3000 is still due in the cycle: principal
        # 3000 - 488.39 = 2511.61, balance 96524.01.
        self.assertEqual(rows[0]['payment_date'], '2026-08-15')
        self.assertEqual(rows[0]['status'], 'partly_paid')
        self.assertEqual(rows[0]['payment'], 3000.0)
        self.assertEqual(rows[0]['interest'], 488.39)
        self.assertEqual(rows[0]['principal'], 2511.61)
        self.assertEqual(rows[0]['remaining'], 96524.01)

    def test_overdue_debt_starts_with_a_row_that_clears_the_overdue(self):
        debt = self.debt()

        rows = build_debt_payment_schedule(debt, today=date(2026, 8, 25))['rows']

        # Overdue 5000 = 1528.77 interest + 3471.23 principal, penalty 27.40.
        overdue = rows[0]
        self.assertEqual(overdue['status'], 'overdue')
        self.assertEqual(overdue['payment_date'], '2026-08-15')
        self.assertEqual(overdue['payment'], 5027.40)
        self.assertEqual(overdue['interest'], 1528.77)
        self.assertEqual(overdue['principal'], 3471.23)
        self.assertEqual(overdue['penalty'], 27.40)
        self.assertEqual(overdue['remaining'], 96528.77)
        # Next regular row on 15.09: current interest 15.08-25.08 on 100000
        # (493.15) plus 25.08-15.09 on 96528.77: 96528.77 * 18% * 21 / 365
        # = 999.67, together 1492.82; principal 5000 - 1492.82 = 3507.18.
        self.assertEqual(rows[1]['payment_date'], '2026-09-15')
        self.assertEqual(rows[1]['interest'], 1492.82)
        self.assertEqual(rows[1]['principal'], 3507.18)
        self.assertEqual(rows[1]['remaining'], 93021.59)

    def test_unpaid_interest_larger_than_the_payment_carries_over(self):
        debt = self.debt(minimum_payment=Decimal('2000.00'), first_payment_amount=Decimal('500.00'))

        rows = build_debt_payment_schedule(debt, today=date(2026, 8, 25))['rows']

        # The 500 first payment was missed: overdue interest 500. Interest left
        # unpaid: 1528.77 - 500 = 1028.77, plus 493.15 since 15.08, plus
        # 25.08-15.09 on 100000: 100000 * 18% * 21 / 365 = 1035.62 -> 2557.54.
        # That is above the 2000 payment, so the row is interest only and
        # 557.54 moves to the next row.
        self.assertEqual(rows[0]['interest'], 500.0)
        self.assertEqual(rows[0]['principal'], 0.0)
        self.assertEqual(rows[1]['interest'], 2000.0)
        self.assertEqual(rows[1]['principal'], 0.0)
        # 15.09-15.10 on 100000: 100000 * 18% * 30 / 365 = 1479.45, plus 557.54
        self.assertEqual(rows[2]['interest'], 2000.0)
        self.assertEqual(rows[2]['principal'], 0.0)
        self.assertGreater(rows[3]['principal'], 0)

    def test_schedule_without_overdue_matches_the_engine_allocation(self):
        debt = self.debt()

        first_row = build_debt_payment_schedule(debt, today=date(2026, 8, 1))['rows'][0]
        state = get_debt_state(debt, as_of=date(2026, 8, 16), payments=[
            Payment(id=1, payment_date=date(2026, 8, 15), amount=Decimal('5000'),
                    principal_amount=Decimal('0'), interest_amount=Decimal('0'),
                    fee_amount=Decimal('0'), penalty_amount=Decimal('0'), breakdown_source='auto'),
        ])

        self.assertEqual(Decimal(str(first_row['interest'])), state['allocations'][1]['interest'])
        self.assertEqual(Decimal(str(first_row['remaining'])), state['principal'])


    def test_split_overdue_plan_and_schedule_agree(self):
        # Four 2004 installments of the old split were paid; a 1738 purchase
        # on 06.08 adds four installments of its own (435, 435, 435, 433)
        # from 27.08. Left: 2002 of the old split due 12.09 and the purchase.
        debt = self.debt(
            debt_type='split', interest_rate=None, total_amount=Decimal('101638.00'),
            remaining_amount=Decimal('11756.00'), minimum_payment=Decimal('2004.00'),
            next_payment_date=date(2026, 7, 20), accrual_start_date=date(2026, 7, 5),
            is_payment_recurring=False, penalty_rate=None,
        )
        for payment_date in (date(2026, 7, 20), date(2026, 7, 27), date(2026, 8, 12), date(2026, 8, 27)):
            self.pay(debt, payment_date, '2004', '2004')
        db.session.add(SplitPurchase(
            debt_id=debt.id, title='Покупка', amount=Decimal('1738.00'),
            purchase_date=date(2026, 8, 6), installments_count=4,
        ))
        db.session.commit()
        self.assertEqual(debt.remaining_amount, Decimal('3740.00'))
        today = date(2026, 10, 13)

        state = get_debt_state(debt, as_of=today)
        schedule = build_debt_payment_schedule(debt, today=today)
        plan = build_financial_plan(self.user_id, today=today)

        overdue_rows = [row for row in schedule['rows'] if row.get('status') == 'overdue']
        # The 27.08 purchase installment was not covered by the 2004 paid that
        # day (it went to the old split), so the overdue starts on 27.08.
        self.assertEqual(state['overdue_since'], date(2026, 8, 27))
        self.assertEqual(state['overdue_amount'], Decimal('3740.00'))
        self.assertEqual(sum(Decimal(str(row['payment'])) for row in overdue_rows), Decimal('3740.00'))
        item = next(item for item in plan['debt_items'] if item['id'] == debt.id)
        self.assertEqual(item['monthly_amount'], 3740.0)
        self.assertEqual(item['overdue_amount'], 3740.0)

    def test_card_without_grace_period_has_no_interest_in_the_schedule(self):
        debt = self.debt(
            debt_type='credit_card', remaining_amount=Decimal('5000.00'),
            minimum_payment=Decimal('3000.00'), interest_rate=Decimal('30.00'),
        )

        rows = build_debt_payment_schedule(debt, today=date(2026, 8, 1))['rows']

        # Same rule as the accrual engine: no grace period end, no interest.
        self.assertEqual([row['interest'] for row in rows], [0.0, 0.0])
        self.assertEqual([row['payment'] for row in rows], [3000.0, 2000.0])

if __name__ == '__main__':
    unittest.main()
