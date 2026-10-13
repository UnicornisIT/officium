"""Accrual engine tests. Expected amounts are worked out by hand in comments:
interest = balance * rate / 100 * days / days_in_year, rounded half up to
kopecks once per period; penalty the same over the overdue amount."""

import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models import Debt
from app.services.debt_accrual_service import get_debt_state
from app.services.debt_schedule_service import build_debt_payment_schedule


D = Decimal


def make_debt(**overrides):
    values = {
        'bank_name': 'Банк',
        'product_name': 'Кредит',
        'debt_type': 'consumer_credit',
        'total_amount': D('100000.00'),
        'remaining_amount': D('100000.00'),
        'minimum_payment': D('5000.00'),
        'interest_rate': D('18.00'),
        'next_payment_date': date(2026, 8, 15),
        'accrual_start_date': date(2026, 7, 15),
        'is_payment_recurring': True,
        'day_count_convention': 'actual_year',
        'include_payment_day': False,
        'penalty_rate': D('20.00'),
        'capitalize_interest': False,
    }
    values.update(overrides)
    return Debt(**values)


_ids = iter(range(1, 10_000))


def pay(payment_date, amount, **overrides):
    # principal_amount=0 means "not applied yet": remaining_amount on the debt
    # is then the principal on the accrual start date.
    values = {
        'id': next(_ids),
        'payment_date': payment_date,
        'amount': D(str(amount)),
        'principal_amount': D('0.00'),
        'interest_amount': D('0.00'),
        'fee_amount': D('0.00'),
        'penalty_amount': D('0.00'),
        'is_early_repayment': False,
        'scheduled_payment_amount': None,
        'breakdown_source': 'auto',
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ConsumerCreditTestCase(unittest.TestCase):
    def test_on_time_full_payment_splits_interest_like_the_schedule(self):
        debt = make_debt()
        payment = pay(date(2026, 8, 15), '5000')

        state = get_debt_state(debt, as_of=date(2026, 8, 16), payments=[payment])

        # Interest 15.07-15.08 = 31 days: 100000 * 18% * 31 / 365 = 1528.767 -> 1528.77
        # Principal 5000 - 1528.77 = 3471.23, balance 96528.77
        allocation = state['allocations'][payment.id]
        self.assertEqual(allocation['interest'], D('1528.77'))
        self.assertEqual(allocation['principal'], D('3471.23'))
        self.assertEqual(state['principal'], D('96528.77'))
        self.assertEqual(state['penalty'], D('0.00'))
        self.assertFalse(state['is_overdue'])
        self.assertTrue(state['cycles'][0]['on_time'])
        # One day after the payment: 96528.77 * 18% / 365 = 47.60
        self.assertEqual(state['current_interest'], D('47.60'))
        self.assertEqual(state['total'], D('96576.37'))
        self.assertEqual(state['next_due_date'], date(2026, 9, 15))
        self.assertEqual(state['next_due_amount'], D('5000.00'))

        schedule_debt = make_debt(interest_period_start_date=date(2026, 7, 15))
        first_row = build_debt_payment_schedule(schedule_debt, today=date(2026, 7, 20))['rows'][0]
        self.assertEqual(D(str(first_row['interest'])), allocation['interest'])
        self.assertEqual(D(str(first_row['remaining'])), state['principal'])

    def test_ten_days_late_payment_pays_overdue_first_and_leaves_penalty(self):
        debt = make_debt()
        payment = pay(date(2026, 8, 25), '5000')

        state = get_debt_state(debt, as_of=date(2026, 8, 25), payments=[payment])

        # 15.08 shortfall 5000 = overdue interest 1528.77 + overdue principal 3471.23.
        # Penalty 15.08-25.08 = 10 days: 5000 * 20% * 10 / 365 = 27.397 -> 27.40
        # The payment covers overdue interest then overdue principal, nothing is
        # left for the penalty. Interest 15.08-25.08 on 100000:
        # 100000 * 18% * 10 / 365 = 493.150 -> 493.15
        allocation = state['allocations'][payment.id]
        self.assertEqual(allocation['overdue_interest'], D('1528.77'))
        self.assertEqual(allocation['overdue_principal'], D('3471.23'))
        self.assertEqual(allocation['penalty'], D('0.00'))
        self.assertFalse(state['is_overdue'])
        self.assertEqual(state['overdue_amount'], D('0.00'))
        self.assertEqual(state['penalty'], D('27.40'))
        self.assertEqual(state['current_interest'], D('493.15'))
        self.assertEqual(state['principal'], D('96528.77'))
        self.assertEqual(state['total'], D('97049.32'))

    def test_late_payment_with_surplus_goes_in_legal_order(self):
        debt = make_debt()
        payment = pay(date(2026, 8, 25), '5100')

        state = get_debt_state(debt, as_of=date(2026, 8, 25), payments=[payment])

        # 1528.77 overdue interest, 3471.23 overdue principal, 27.40 penalty,
        # 72.60 of the current 493.15 interest, nothing to principal.
        allocation = state['allocations'][payment.id]
        self.assertEqual(
            (allocation['overdue_interest'], allocation['overdue_principal'],
             allocation['penalty'], allocation['current_interest']),
            (D('1528.77'), D('3471.23'), D('27.40'), D('72.60')),
        )
        self.assertEqual(state['penalty'], D('0.00'))
        self.assertEqual(state['current_interest'], D('420.55'))
        self.assertEqual(state['principal'], D('96528.77'))

    def test_money_that_settled_an_overdue_does_not_pay_the_next_cycle(self):
        debt = make_debt()
        payments = [pay(date(2026, 8, 25), '5000'), pay(date(2026, 9, 15), '5000')]

        state = get_debt_state(debt, as_of=date(2026, 8, 26), payments=payments[:1])
        # The late 5000 closed the 15.08 overdue, the 15.09 payment is still due.
        self.assertEqual(state['next_due_date'], date(2026, 9, 15))
        self.assertEqual(state['next_due_amount'], D('5000.00'))

        state = get_debt_state(debt, as_of=date(2026, 9, 16), payments=payments)
        # 25.08 money went to the 15.08 overdue, so it is not credited to the
        # 15.09 cycle. The 15.09 payment first pays the 27.40 penalty left from
        # August (legal order), so 4972.60 reaches the installment and 27.40 of
        # it becomes overdue.
        self.assertEqual(state['cycles'][1]['paid'], D('4972.60'))
        self.assertEqual(state['cycles'][1]['shortfall'], D('27.40'))
        self.assertTrue(state['is_overdue'])
        self.assertEqual(state['next_due_date'], date(2026, 9, 15))
        self.assertEqual(state['next_due_amount'], D('27.40'))

    def test_two_partial_payments_before_due_date_are_on_time(self):
        debt = make_debt()
        payments = [pay(date(2026, 8, 5), '2000'), pay(date(2026, 8, 15), '3000')]

        state = get_debt_state(debt, as_of=date(2026, 8, 20), payments=payments)

        # 15.07-05.08 = 21 days: 100000 * 18% * 21 / 365 = 1035.616 -> 1035.62,
        # principal 964.38, balance 99035.62.
        # 05.08-15.08 = 10 days: 99035.62 * 18% * 10 / 365 = 488.395 -> 488.39,
        # principal 2511.61, balance 96524.01. Paid in cycle 5000 >= 5000.
        self.assertTrue(state['cycles'][0]['on_time'])
        self.assertFalse(state['is_overdue'])
        self.assertEqual(state['penalty'], D('0.00'))
        self.assertEqual(state['allocations'][payments[0].id]['interest'], D('1035.62'))
        self.assertEqual(state['allocations'][payments[1].id]['interest'], D('488.39'))
        self.assertEqual(state['principal'], D('96524.01'))

    def test_partial_before_due_and_top_up_after_charges_penalty_on_the_gap_only(self):
        debt = make_debt()
        payments = [pay(date(2026, 8, 10), '3000'), pay(date(2026, 8, 20), '2000')]

        state = get_debt_state(debt, as_of=date(2026, 8, 20), payments=payments)

        # 15.07-10.08 = 26 days: 100000 * 18% * 26 / 365 = 1282.192 -> 1282.19,
        # principal 1717.81, balance 98282.19.
        # 10.08-15.08 = 5 days: 98282.19 * 18% * 5 / 365 = 242.340 -> 242.34.
        # Shortfall 2000 = overdue interest 242.34 + overdue principal 1757.66.
        # Penalty 15.08-20.08 = 5 days on 2000: 2000 * 20% * 5 / 365 = 5.479 -> 5.48
        self.assertEqual(state['cycles'][0]['shortfall'], D('2000.00'))
        allocation = state['allocations'][payments[1].id]
        self.assertEqual(allocation['overdue_interest'], D('242.34'))
        self.assertEqual(allocation['overdue_principal'], D('1757.66'))
        self.assertFalse(state['is_overdue'])
        self.assertEqual(state['penalty'], D('5.48'))

    def test_unpaid_installment_is_overdue_with_amount_to_clear(self):
        debt = make_debt()

        state = get_debt_state(debt, as_of=date(2026, 8, 25), payments=[])

        # Same numbers as the ten-day case without the payment.
        self.assertTrue(state['is_overdue'])
        self.assertEqual(state['overdue_since'], date(2026, 8, 15))
        self.assertEqual(state['overdue_days'], 10)
        self.assertEqual(state['overdue_interest'], D('1528.77'))
        self.assertEqual(state['overdue_principal'], D('3471.23'))
        self.assertEqual(state['amount_to_clear_overdue'], D('5027.40'))
        self.assertEqual(state['next_due_date'], date(2026, 8, 15))
        # Interest keeps accruing on the whole principal.
        self.assertEqual(state['accrued_interest'], D('2021.92'))
        self.assertEqual(state['total'], D('102049.32'))

    def test_rate_change_and_new_year_inside_overdue_period(self):
        debt = make_debt(
            next_payment_date=date(2026, 12, 20),
            accrual_start_date=date(2026, 11, 20),
            interest_rate_after_change=D('24.00'),
            interest_rate_change_date=date(2027, 1, 10),
        )

        state = get_debt_state(debt, as_of=date(2027, 1, 20), payments=[])

        # 20.11-20.12 = 30 days: 100000 * 18% * 30 / 365 = 1479.452 -> 1479.45 overdue
        # interest, overdue principal 3520.55.
        # Penalty on 5000: 20.12-01.01 12 days / 365 = 32.877 and
        # 01.01-20.01 19 days / 365 = 52.055 -> 84.93
        # Interest after the due date on 100000:
        # 12 days @18% = 591.781, 9 days @18% = 443.836, 10 days @24% = 657.534
        # -> 1693.15
        self.assertEqual(state['overdue_interest'], D('1479.45'))
        self.assertEqual(state['overdue_principal'], D('3520.55'))
        self.assertEqual(state['penalty'], D('84.93'))
        self.assertEqual(state['current_interest'], D('1693.15'))
        penalty_years = {period['start'].year for period in state['periods'] if period['kind'] == 'penalty'}
        self.assertEqual(penalty_years, {2026, 2027})
        rates = {period['rate'] for period in state['periods'] if period['kind'] == 'interest'}
        self.assertEqual(rates, {D('18.00'), D('24.00')})

    def test_day_count_conventions_in_leap_and_common_years(self):
        def interest(convention, year):
            debt = make_debt(
                interest_rate=D('10.00'),
                day_count_convention=convention,
                is_payment_recurring=False,
                next_payment_date=date(year, 3, 15),
                accrual_start_date=date(year, 2, 1),
            )
            return get_debt_state(debt, as_of=date(year, 3, 1), payments=[])['current_interest']

        # 2028 is leap: 29 days. 100000 * 10% * 29 / 366 = 792.350; / 365 = 794.521
        self.assertEqual(interest('actual_year', 2028), D('792.35'))
        self.assertEqual(interest('fixed_365', 2028), D('794.52'))
        self.assertEqual(interest('fixed_366', 2028), D('792.35'))
        # 2027: 28 days. / 365 = 767.123; / 366 = 765.027
        self.assertEqual(interest('actual_year', 2027), D('767.12'))
        self.assertEqual(interest('fixed_365', 2027), D('767.12'))
        self.assertEqual(interest('fixed_366', 2027), D('765.03'))

    def test_penalty_uses_the_debt_day_count(self):
        debt = make_debt(
            day_count_convention='fixed_366',
            next_payment_date=date(2026, 8, 15),
        )

        state = get_debt_state(debt, as_of=date(2026, 8, 25), payments=[])

        # 5000 * 20% * 10 / 366 = 27.322 -> 27.32
        self.assertEqual(state['penalty'], D('27.32'))

    def test_no_penalty_rate_means_no_penalty(self):
        debt = make_debt(penalty_rate=None)

        state = get_debt_state(debt, as_of=date(2026, 8, 25), payments=[])

        self.assertTrue(state['is_overdue'])
        self.assertEqual(state['penalty'], D('0.00'))
        self.assertIsNone(state['penalty_rate'])

    def test_editing_and_deleting_a_payment_changes_the_penalty(self):
        debt = make_debt()

        moved = get_debt_state(debt, as_of=date(2026, 8, 25), payments=[pay(date(2026, 8, 20), '5000')])
        deleted = get_debt_state(debt, as_of=date(2026, 8, 25), payments=[])

        # Paid five days late instead of ten: 5000 * 20% * 5 / 365 = 13.699 -> 13.70
        self.assertEqual(moved['penalty'], D('13.70'))
        self.assertFalse(moved['is_overdue'])
        # Without the payment the overdue stays open and the penalty runs to 25.08.
        self.assertEqual(deleted['penalty'], D('27.40'))
        self.assertTrue(deleted['is_overdue'])

    def test_capitalization_grows_principal_by_interest_only(self):
        without = get_debt_state(make_debt(), as_of=date(2026, 8, 25), payments=[])
        with_cap = get_debt_state(make_debt(capitalize_interest=True), as_of=date(2026, 8, 25), payments=[])

        self.assertEqual(without['principal'], D('100000.00'))
        self.assertEqual(without['capitalized_interest'], D('0.00'))
        # 1528.77 of period interest moves into principal: 101528.77. The 5000
        # shortfall is principal; the penalty 27.40 is not capitalized.
        self.assertEqual(with_cap['principal'], D('101528.77'))
        self.assertEqual(with_cap['capitalized_interest'], D('1528.77'))
        self.assertEqual(with_cap['overdue_principal'], D('5000.00'))
        self.assertEqual(with_cap['overdue_interest'], D('0.00'))
        self.assertEqual(with_cap['penalty'], D('27.40'))
        # 101528.77 * 18% * 10 / 365 = 500.690 -> 500.69
        self.assertEqual(with_cap['current_interest'], D('500.69'))

    def test_manual_breakdown_takes_priority(self):
        debt = make_debt()
        payment = pay(
            date(2026, 8, 15), '5000',
            principal_amount=D('4000.00'), interest_amount=D('1000.00'), breakdown_source='manual',
        )

        state = get_debt_state(debt, as_of=date(2026, 8, 15), payments=[payment])

        # remaining_amount already reflects the stored 4000 principal, so the
        # opening principal is 96000 + 4000 = 100000 and the balance is back at 96000.
        # The unpaid rest of the period interest 1528.77 - 1000 = 528.77 carries over.
        self.assertEqual(state['allocations'][payment.id]['principal'], D('4000.00'))
        self.assertEqual(state['allocations'][payment.id]['source'], 'manual')
        debt.remaining_amount = D('96000.00')
        state = get_debt_state(debt, as_of=date(2026, 8, 16), payments=[payment])
        self.assertEqual(state['opening_principal'], D('100000.00'))
        self.assertEqual(state['principal'], D('96000.00'))
        self.assertFalse(state['is_overdue'])
        # 528.77 + one day on 96000: 96000 * 18% / 365 = 47.342 -> 576.11
        self.assertEqual(state['current_interest'], D('576.11'))

    def test_early_repayment_goes_to_principal_and_does_not_cover_the_cycle(self):
        debt = make_debt()
        payment = pay(date(2026, 8, 1), '10000', is_early_repayment=True)

        state = get_debt_state(debt, as_of=date(2026, 8, 16), payments=[payment])

        self.assertEqual(state['allocations'][payment.id]['principal'], D('10000.00'))
        self.assertEqual(state['allocations'][payment.id]['interest'], D('0.00'))
        self.assertEqual(state['cycles'][0]['paid'], D('0.00'))
        self.assertTrue(state['is_overdue'])

    def test_first_payment_amount_is_required_in_the_first_cycle_only(self):
        debt = make_debt(first_payment_amount=D('2000.00'))

        state = get_debt_state(debt, as_of=date(2026, 9, 16), payments=[
            pay(date(2026, 8, 15), '2000'),
            pay(date(2026, 9, 15), '5000'),
        ])

        self.assertEqual([cycle['required'] for cycle in state['cycles']], [D('2000.00'), D('5000.00')])
        self.assertFalse(state['is_overdue'])

    def test_payments_on_or_before_accrual_start_are_settled_history(self):
        debt = make_debt()
        old = pay(date(2026, 7, 1), '5000', principal_amount=D('5000.00'))

        state = get_debt_state(debt, as_of=date(2026, 7, 16), payments=[old])

        self.assertEqual(state['opening_principal'], D('100000.00'))
        self.assertNotIn(old.id, state['allocations'])

    def test_include_payment_day_does_not_count_a_day_twice(self):
        payments = [pay(date(2026, 8, 15), '5000'), pay(date(2026, 9, 15), '5000')]
        plain = get_debt_state(make_debt(), as_of=date(2026, 9, 16), payments=payments)
        shifted = get_debt_state(make_debt(include_payment_day=True), as_of=date(2026, 9, 16), payments=payments)

        def interest_days(state):
            return sum(period['days'] for period in state['periods'] if period['kind'] == 'interest')

        # 15.07-16.09 is 63 days either way; only the first and last day differ.
        self.assertEqual(interest_days(plain), 63)
        self.assertEqual(interest_days(shifted), 63)


class CreditCardTestCase(unittest.TestCase):
    def card(self, **overrides):
        values = {
            'debt_type': 'credit_card',
            'remaining_amount': D('30000.00'),
            'total_amount': D('100000.00'),
            'minimum_payment': D('3000.00'),
            'interest_rate': D('30.00'),
            'next_payment_date': date(2026, 6, 25),
            'accrual_start_date': date(2026, 6, 1),
            'interest_period_start_date': date(2026, 6, 1),
            'grace_period_end_date': date(2026, 8, 31),
        }
        values.update(overrides)
        return make_debt(**values)

    def test_grace_balance_repaid_in_time_has_no_interest(self):
        state = get_debt_state(self.card(), as_of=date(2026, 9, 5), payments=[
            pay(date(2026, 6, 20), '3000'),
            pay(date(2026, 7, 20), '3000'),
            pay(date(2026, 8, 14), '24000'),
        ])

        self.assertEqual(state['accrued_interest'], D('0.00'))
        self.assertEqual(state['principal'], D('0.00'))
        self.assertEqual(state['total'], D('0.00'))

    def test_grace_balance_not_repaid_charges_interest_from_the_start(self):
        state = get_debt_state(self.card(), as_of=date(2026, 9, 5), payments=[
            pay(date(2026, 6, 20), '3000'),
            pay(date(2026, 7, 20), '3000'),
            pay(date(2026, 8, 20), '3000'),
        ])

        # Minimums are paid on time, but 21000 is left after the grace period:
        # 01.06-20.06 19 days on 30000: 30000 * 30% * 19 / 365 = 468.493
        # 20.06-20.07 30 days on 27000: 27000 * 30% * 30 / 365 = 665.753
        # 20.07-20.08 31 days on 24000: 24000 * 30% * 31 / 365 = 611.507
        # 20.08-01.09 12 days on 21000: 21000 * 30% * 12 / 365 = 207.123
        # 01.09-05.09  4 days on 21000: 21000 * 30% *  4 / 365 =  69.041
        # Total 2021.918 -> 2021.92
        self.assertEqual(state['accrued_interest'], D('2021.92'))
        self.assertEqual(state['principal'], D('21000.00'))
        self.assertFalse(state['is_overdue'])

    def test_during_grace_interest_is_only_pending(self):
        state = get_debt_state(self.card(), as_of=date(2026, 7, 1), payments=[])

        # 01.06-01.07 30 days: 30000 * 30% * 30 / 365 = 739.726 -> 739.73
        self.assertEqual(state['accrued_interest'], D('0.00'))
        self.assertEqual(state['grace_interest_pending'], D('739.73'))

    def test_card_without_grace_period_has_no_interest_but_minimum_is_enforced(self):
        card = self.card(grace_period_end_date=None)

        state = get_debt_state(card, as_of=date(2026, 7, 5), payments=[])

        # Missed 3000 minimum on 25.06: 3000 * 20% * 10 / 365 = 16.438 -> 16.44
        self.assertEqual(state['accrued_interest'], D('0.00'))
        self.assertEqual(state['overdue_principal'], D('3000.00'))
        self.assertEqual(state['penalty'], D('16.44'))


class SplitTestCase(unittest.TestCase):
    def test_split_overdue_without_penalty_rate_charges_nothing(self):
        debt = make_debt(
            debt_type='split',
            remaining_amount=D('4000.00'),
            minimum_payment=D('1000.00'),
            interest_rate=D('58.25'),
            penalty_rate=None,
            next_payment_date=date(2026, 8, 12),
            accrual_start_date=date(2026, 7, 27),
            is_payment_recurring=False,
        )

        state = get_debt_state(debt, as_of=date(2026, 9, 1), payments=[])

        # Due 12.08 and 27.08 missed: 2000 overdue, no interest, no penalty.
        self.assertEqual(state['overdue_principal'], D('2000.00'))
        self.assertEqual(state['overdue_days'], 20)
        self.assertEqual(state['accrued_interest'], D('0.00'))
        self.assertEqual(state['penalty'], D('0.00'))
        self.assertEqual(state['total'], D('4000.00'))

    def test_split_with_penalty_rate_charges_penalty(self):
        debt = make_debt(
            debt_type='split',
            remaining_amount=D('4000.00'),
            minimum_payment=D('1000.00'),
            penalty_rate=D('36.50'),
            next_payment_date=date(2026, 8, 12),
            accrual_start_date=date(2026, 7, 27),
        )

        state = get_debt_state(debt, as_of=date(2026, 8, 22), payments=[])

        # 1000 overdue for 10 days: 1000 * 36.5% * 10 / 365 = 10.00
        self.assertEqual(state['penalty'], D('10.00'))


if __name__ == '__main__':
    unittest.main()
