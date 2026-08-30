import unittest
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from app import create_app
from app.models import Debt, Payment, User
from app.services.debt_math_service import calculate_period_interest
from app.services.payment_service import add_payment
from app.utils import parse_decimal
from extensions import db


MONEY = Decimal('0.01')


def debt_math_fixture(**overrides):
    values = {
        'interest_rate': Decimal('10.00'),
        'interest_rate_after_change': None,
        'interest_rate_change_date': None,
        'day_count_convention': 'actual_year',
        'include_payment_day': False,
    }
    values.update(overrides)
    debt = SimpleNamespace(**values)

    def interest_rate_for(value_date):
        if (
            debt.interest_rate_after_change is not None
            and debt.interest_rate_change_date is not None
            and value_date >= debt.interest_rate_change_date
        ):
            return debt.interest_rate_after_change
        return debt.interest_rate

    debt.interest_rate_for = interest_rate_for
    return debt


class DebtMathIntegrityTestCase(unittest.TestCase):
    def test_actual_year_uses_366_days_in_leap_year(self):
        debt = debt_math_fixture()

        interest = calculate_period_interest(
            debt,
            Decimal('100000.00'),
            date(2024, 1, 1),
            date(2024, 1, 2),
        )

        self.assertEqual(interest, Decimal('27.32'))

    def test_period_is_split_at_year_and_rate_boundaries(self):
        debt = debt_math_fixture(
            interest_rate_after_change=Decimal('20.00'),
            interest_rate_change_date=date(2024, 1, 2),
        )
        expected = (
            Decimal('100000') * Decimal('10') / Decimal('100') / Decimal('365')
            + Decimal('100000') * Decimal('10') / Decimal('100') / Decimal('366')
            + Decimal('100000') * Decimal('20') / Decimal('100') / Decimal('366')
        ).quantize(MONEY, rounding=ROUND_HALF_UP)

        interest = calculate_period_interest(
            debt,
            Decimal('100000.00'),
            date(2023, 12, 31),
            date(2024, 1, 3),
        )

        self.assertEqual(interest, expected)

    def test_fixed_day_count_and_payment_day_are_honored(self):
        debt = debt_math_fixture(day_count_convention='fixed_365', include_payment_day=True)

        interest = calculate_period_interest(
            debt,
            Decimal('100000.00'),
            date(2024, 1, 1),
            date(2024, 1, 2),
        )

        self.assertEqual(interest, Decimal('54.79'))

    def test_decimal_parser_rounds_half_up_and_rejects_non_finite_values(self):
        self.assertEqual(parse_decimal('10.005', 'Сумма'), Decimal('10.01'))
        for value in ('NaN', 'Infinity', '-Infinity', '10000000000'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_decimal(value, 'Сумма')


class PaymentIntegrityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        user = User(telegram_id=707070, first_name='Money', role='user')
        db.session.add(user)
        db.session.flush()
        self.user_id = user.id
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _debt(self, remaining=Decimal('1000.00')):
        debt = Debt(
            user_id=self.user_id,
            bank_name='Test Bank',
            debt_type='consumer_credit',
            product_name='Test loan',
            total_amount=Decimal('1000.00'),
            remaining_amount=remaining,
            minimum_payment=Decimal('1000.00'),
            interest_rate=Decimal('36.50'),
            next_payment_date=date(2026, 1, 1),
            day_count_convention='fixed_365',
            is_payment_recurring=False,
            status='active',
        )
        db.session.add(debt)
        db.session.commit()
        return debt

    def test_explicit_breakdown_must_match_to_the_cent(self):
        debt = self._debt()

        with self.assertRaisesRegex(ValueError, 'должны совпадать'):
            add_payment(
                debt,
                Decimal('100.00'),
                payment_date=date(2026, 1, 1),
                principal_amount=Decimal('70.00'),
                interest_amount=Decimal('29.99'),
                fee_amount=Decimal('0.00'),
            )

        self.assertEqual(Payment.query.count(), 0)

    def test_overpayment_and_payment_of_closed_debt_are_rejected(self):
        debt = self._debt()
        with self.assertRaisesRegex(ValueError, 'превышает остаток'):
            add_payment(debt, Decimal('1010.01'), payment_date=date(2026, 1, 11))

        debt.remaining_amount = Decimal('0.00')
        db.session.commit()
        with self.assertRaisesRegex(ValueError, 'полностью погашен'):
            add_payment(debt, Decimal('1.00'), payment_date=date(2026, 1, 11))

    def test_two_partial_late_payments_do_not_charge_same_interest_twice(self):
        debt = self._debt()

        first = add_payment(debt, Decimal('5.00'), payment_date=date(2026, 1, 11))
        second = add_payment(debt, Decimal('5.00'), payment_date=date(2026, 1, 11))

        self.assertEqual(first.interest_amount, Decimal('5.00'))
        self.assertEqual(second.interest_amount, Decimal('5.00'))
        self.assertEqual(first.principal_amount + second.principal_amount, Decimal('0.00'))
        self.assertEqual(debt.remaining_amount, Decimal('1000.00'))

    def test_service_rejects_non_finite_payment(self):
        debt = self._debt()
        with self.assertRaisesRegex(ValueError, 'некорректное число'):
            add_payment(debt, Decimal('NaN'), payment_date=date(2026, 1, 1))


if __name__ == '__main__':
    unittest.main()
