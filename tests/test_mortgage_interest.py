import unittest
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from app.services.debt_accrual_service import calculate_accrued_interest, get_debt_state
from app.services.debt_math_service import calculate_period_interest
from app.services.mortgage_interest_service import mortgage_month_interest
from tests.test_debt_accrual import make_debt, pay
from tests.test_finance_summary import FinanceSummaryTestCase
from extensions import db
from app.models import Payment
from app.services.finance_summary_service import get_finance_summary


def mortgage(**overrides):
    values = dict(
        debt_type='mortgage',
        opening_principal_amount=D('100000'),
        accrual_start_date=date(2026, 11, 1),
        next_payment_date=date(2026, 11, 25),
        interest_rate=D('36.5'),
        minimum_payment=D('10000'),
        penalty_rate=D('0'),
    )
    values.update(overrides)
    return make_debt(**values)


class MortgageInterestTests(unittest.TestCase):
    def test_early_and_scheduled_payments_reduce_only_later_segments(self):
        debt = mortgage()
        payments = [
            pay(date(2026, 11, 10), '20000', is_early_repayment=True),
            pay(date(2026, 11, 25), '10000'),
        ]
        state = get_debt_state(debt, date(2026, 12, 1), payments)
        # 9 * 100 + 15 * 80 + 6 * 72.10 = 2532.60.
        actual = calculate_accrued_interest(
            debt, date(2026, 11, 1), date(2026, 12, 1), payments
        )
        self.assertEqual(actual, D('2532.60'))
        self.assertEqual(
            actual,
            sum(p['amount'] for p in state['periods'] if p['kind'] == 'interest'),
        )
        self.assertEqual(state['allocations'][payments[1].id]['interest'], D('2100'))
        debt.remaining_amount = state['principal']
        self.assertNotEqual(
            actual,
            calculate_period_interest(
                debt, debt.remaining_amount, date(2026, 11, 1), date(2026, 12, 1)
            ),
        )

    def test_rate_change(self):
        debt = mortgage(
            interest_rate_after_change=D('73'),
            interest_rate_change_date=date(2026, 11, 16),
        )
        self.assertEqual(
            calculate_accrued_interest(debt, date(2026, 11, 1), date(2026, 12, 1), []),
            D('4500'),
        )

    def test_year_boundary_uses_each_year_denominator(self):
        debt = mortgage(
            accrual_start_date=date(2024, 12, 31), next_payment_date=date(2025, 1, 25)
        )
        expected = (D('36500') / 366 + D('100')).quantize(D('.01'))
        self.assertEqual(
            calculate_accrued_interest(debt, date(2024, 12, 31), date(2025, 1, 2), []),
            expected,
        )

    def test_clips_segments_and_rounds_once(self):
        debt = mortgage(interest_rate=D('18'), next_payment_date=date(2026, 12, 25))
        self.assertEqual(
            calculate_accrued_interest(debt, date(2026, 11, 5), date(2026, 11, 8), []),
            D('147.95'),
        )
        self.assertEqual(
            calculate_accrued_interest(debt, date(2026, 10, 1), date(2026, 11, 1), []),
            D('0'),
        )

    def test_future_month_uses_schedule_principal(self):
        debt = mortgage()
        result = mortgage_month_interest(
            [debt], date(2026, 12, 1), date(2027, 1, 1), date(2026, 11, 1)
        )
        # Nov 25: principal 100000 -> 92400; Dec 25: -> 85172.
        self.assertEqual(
            result,
            (D('92400') * D('.001') * 24 + D('85172') * D('.001') * 7).quantize(
                D('.01')
            ),
        )

    def test_unavailable_schedule_does_not_become_zero_forecast(self):
        debt = mortgage(minimum_payment=D('0'))
        self.assertIsNone(
            mortgage_month_interest(
                [debt], date(2026, 12, 1), date(2027, 1, 1), date(2026, 11, 1)
            )
        )


class MortgageSummaryTests(FinanceSummaryTestCase):
    def test_closed_month_is_stable_and_current_month_stops_at_today(self):
        with self.app.app_context(), patch(
            'app.services.finance_summary_service.financial_today'
        ) as clock:
            debt = mortgage(user_id=self.user_id)
            debt.remaining_amount = D('80000')
            debt.payments.append(
                Payment(
                    amount=D('20000'),
                    principal_amount=D('20000'),
                    interest_amount=D('0'),
                    fee_amount=D('0'),
                    penalty_amount=D('0'),
                    breakdown_source='manual',
                    is_early_repayment=True,
                    payment_date=date(2026, 11, 10),
                    remaining_after_payment=D('80000'),
                )
            )
            db.session.add(debt)
            db.session.commit()
            clock.return_value = date(2026, 11, 10)
            current = get_finance_summary(self.user_id, 2026, 11)
            self.assertEqual(current['total_mortgage_interest'], D('900'))
            self.assertFalse(current['mortgage_interest_is_estimate'])
            clock.return_value = date(2026, 12, 1)
            closed = get_finance_summary(self.user_id, 2026, 11)
            self.assertEqual(closed['total_mortgage_interest'], D('2580'))
            debt.payments.append(
                Payment(
                    amount=D('10000'),
                    principal_amount=D('10000'),
                    interest_amount=D('0'),
                    fee_amount=D('0'),
                    penalty_amount=D('0'),
                    breakdown_source='manual',
                    is_early_repayment=True,
                    payment_date=date(2026, 12, 10),
                    remaining_after_payment=D('70000'),
                )
            )
            debt.remaining_amount = D('70000')
            db.session.commit()
            clock.return_value = date(2027, 1, 10)
            self.assertEqual(
                get_finance_summary(self.user_id, 2026, 11)['total_mortgage_interest'],
                closed['total_mortgage_interest'],
            )
            future = get_finance_summary(self.user_id, 2027, 2)
            self.assertTrue(future['mortgage_interest_is_estimate'])
