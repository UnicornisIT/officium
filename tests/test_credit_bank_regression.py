"""Bank statement regression and replay invariants, with a controlled clock."""

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app
from app.models import Payment, User
from app.services.debt_accrual_service import get_debt_state
from app.services.debt_math_service import calculate_period_interest
from app.services.debt_calendar_service import next_payment_due_date
from app.services.debt_schedule_service import build_debt_payment_schedule
from app.services.payment_service import add_payment, delete_payment, recalculate_debt, update_payment
from app.time_utils import financial_today
from extensions import db
from tests.test_debt_accrual import make_debt, pay


D = Decimal
CHECK_DATE = date(2026, 11, 5)
INSTANT = datetime(2026, 11, 4, 23, 12, tzinfo=timezone.utc)


def bank_debt(**overrides):
    values = dict(
        total_amount=D('500000.00'),
        remaining_amount=D('500000.00'),
        opening_principal_amount=D('500000.00'),
        interest_rate=D('30.90'),
        minimum_payment=D('16454.19'),
        first_payment_amount=D('2539.73'),
        next_payment_date=date(2026, 8, 28),
        accrual_start_date=date(2026, 8, 22),
        interest_period_start_date=date(2026, 8, 22),
        day_count_convention='fixed_365',
        repayment_type='annuity',
        penalty_rate=None,
    )
    values.update(overrides)
    return make_debt(**values)


def statement():
    return [
        pay(date(2026, 8, 28), '2539.73'),
        pay(date(2026, 9, 28), '25001.81', is_early_repayment=True, scheduled_payment_amount=D('16616.48')),
        pay(date(2026, 10, 28), '25000', is_early_repayment=True, scheduled_payment_amount=D('16454.19')),
    ]


class BankReplayTests(unittest.TestCase):
    def test_statement_allocations_and_checkpoint(self):
        payments = statement()
        state = get_debt_state(bank_debt(), as_of=CHECK_DATE, payments=payments)
        expected = [
            ('2539.73', '0.00', '500000.00'),
            ('13121.92', '11879.89', '488120.11'),
            ('12396.91', '12603.09', '475517.02'),
        ]
        for payment, parts in zip(payments, expected):
            with self.subTest(date=payment.payment_date):
                row = state['allocations'][payment.id]
                self.assertEqual(
                    tuple(row[k] for k in ('interest', 'principal', 'remaining_after')), tuple(map(D, parts))
                )
                self.assertEqual(row['interest'] + row['principal'], payment.amount)
        self.assertEqual(state['principal'], D('475517.02'))
        self.assertEqual(state['accrued_interest'], D('3220.49'))
        self.assertEqual(state['total'], D('478737.51'))
        self.assertEqual(state['next_due_date'], date(2026, 11, 28))
        self.assertEqual(state['next_due_amount'], D('16454.19'))
        self.assertFalse(state['is_overdue'])

    def test_local_midnight_reproduces_and_fixes_missing_day(self):
        payments = statement()
        account = SimpleNamespace(timezone='Europe/Moscow')
        with patch('app.time_utils.utc_now', return_value=INSTANT):
            debt = bank_debt()
            debt.user = User(timezone=account.timezone)
            state = get_debt_state(debt, payments=payments)
        self.assertEqual(financial_today(account, now=INSTANT), CHECK_DATE)
        self.assertEqual(INSTANT.date(), date(2026, 11, 4))
        self.assertEqual(state['accrued_interest'], D('3220.49'))
        wrong = get_debt_state(
            bank_debt(
                remaining_amount=D('450000'), opening_principal_amount=None, accrual_start_date=date(2026, 10, 28)
            ),
            as_of=INSTANT.date(),
            payments=[],
        )
        self.assertEqual(wrong['accrued_interest'], D('2666.71'))

    def test_timezone_changes_calendar_date_without_changing_explicit_checkpoint(self):
        for zone, expected in [
            ('UTC', date(2026, 11, 4)),
            ('Europe/Moscow', CHECK_DATE),
            ('America/New_York', date(2026, 11, 4)),
        ]:
            with self.subTest(zone=zone):
                self.assertEqual(financial_today(SimpleNamespace(timezone=zone), now=INSTANT), expected)
                debt = bank_debt()
                debt.user = User(timezone=zone)
                self.assertEqual(get_debt_state(debt, as_of=CHECK_DATE, payments=statement())['total'], D('478737.51'))

    def test_replay_is_idempotent_sorted_and_deduplicated_by_id(self):
        debt, payments = bank_debt(), statement()
        first = get_debt_state(debt, as_of=CHECK_DATE, payments=payments)
        second = get_debt_state(debt, as_of=CHECK_DATE, payments=[payments[2], payments[0], payments[1], payments[0]])
        self.assertEqual(first, second)
        self.assertEqual(get_debt_state(debt, as_of=CHECK_DATE, payments=payments), first)
        self.assertEqual(debt.remaining_amount, D('500000'))

    def test_duplicate_identifier_with_different_data_is_rejected(self):
        payment = statement()[0]
        duplicate = pay(payment.payment_date, '1', id=payment.id)
        with self.assertRaises(ValueError):
            get_debt_state(bank_debt(), as_of=CHECK_DATE, payments=[payment, duplicate])

    def test_anchor_is_independent_of_corrupt_cached_balance(self):
        state = get_debt_state(bank_debt(remaining_amount=D('450000')), as_of=CHECK_DATE, payments=statement())
        self.assertEqual(state['principal'], D('475517.02'))

    def test_saved_history_conflict_is_reported_without_mutation(self):
        payment = pay(
            date(2026, 8, 28), '2539.73', principal_amount=D('2539.73'), remaining_after_payment=D('474613.97')
        )
        state = get_debt_state(bank_debt(), as_of=CHECK_DATE, payments=[payment])
        self.assertFalse(state['data_consistent'])
        self.assertTrue(state['diagnostics'])
        self.assertEqual(payment.principal_amount, D('2539.73'))
        self.assertEqual(payment.remaining_after_payment, D('474613.97'))

    def test_closed_legacy_history_from_working_app_is_not_silently_trusted(self):
        payments = statement()
        for payment, principal, interest, remaining in zip(
            payments,
            ['2539.73', '11944.73', '12669.24'],
            ['0', '13055.27', '12330.76'],
            ['474613.97', '462669.24', '450000'],
        ):
            payment.principal_amount, payment.interest_amount = D(principal), D(interest)
            payment.remaining_after_payment = D(remaining)
        payments[1].amount = D('25000')
        debt = bank_debt(
            opening_principal_amount=None,
            remaining_amount=D('450000'),
            accrual_start_date=date(2026, 10, 28),
            next_payment_date=date(2026, 11, 28),
        )
        state = get_debt_state(debt, as_of=CHECK_DATE, payments=payments)
        self.assertFalse(state['data_consistent'])
        self.assertEqual(state['principal'], D('450000'))

    def test_actual_saved_september_amount_cannot_match_bank_statement(self):
        payments = statement()
        payments[1].amount = D('25000')
        state = get_debt_state(bank_debt(), as_of=CHECK_DATE, payments=payments)
        self.assertEqual(state['principal'], D('475518.88'))
        self.assertEqual(state['total'], D('478739.38'))

    def test_leap_year_conventions_and_adjacent_periods(self):
        debt = bank_debt(interest_rate=D('10'))
        start, middle, end = date(2024, 2, 28), date(2024, 2, 29), date(2024, 3, 1)
        self.assertEqual(calculate_period_interest(debt, D('36500'), start, end), D('20.00'))
        self.assertEqual(
            sum(calculate_period_interest(debt, D('36500'), a, b) for a, b in [(start, middle), (middle, end)]),
            D('20.00'),
        )
        debt.day_count_convention = 'actual_year'
        self.assertEqual(calculate_period_interest(debt, D('36600'), start, end), D('20.00'))

    def test_projection_preserves_month_end(self):
        debt = bank_debt(
            first_payment_amount=None,
            next_payment_date=date(2027, 1, 31),
            accrual_start_date=date(2026, 12, 31),
            interest_period_start_date=date(2026, 12, 31),
        )
        rows = build_debt_payment_schedule(debt, today=date(2027, 1, 1))['rows']
        self.assertEqual([row['payment_date'] for row in rows[:3]], ['2027-01-31', '2027-02-28', '2027-03-31'])

    def test_calendar_retains_contract_day_after_short_month(self):
        debt = bank_debt(payment_day=31, next_payment_date=date(2027, 2, 28))
        self.assertEqual(next_payment_due_date(debt, date(2027, 2, 28)), date(2027, 3, 31))

    def test_small_first_payment_carries_unpaid_interest_to_next_row(self):
        debt = bank_debt(accrual_start_date=date(2026, 7, 28), interest_period_start_date=date(2026, 7, 28))
        rows = build_debt_payment_schedule(debt, today=date(2026, 8, 1))['rows']
        # First month costs 13121.92; after 2539.73 the unpaid 10582.19
        # must remain owed in addition to the next month's 13121.92.
        self.assertEqual(rows[0]['principal'], 0)
        self.assertEqual(rows[1]['principal'], 0)
        self.assertEqual(rows[1]['interest'], 16454.19)

    def test_full_early_payoff_settles_interest_and_stops_accrual(self):
        payments = statement()
        payments.append(pay(CHECK_DATE, '478737.51', is_early_repayment=True))
        state = get_debt_state(bank_debt(), as_of=date(2026, 12, 5), payments=payments)
        self.assertEqual((state['principal'], state['accrued_interest'], state['total']), (D('0'),) * 3)
        self.assertEqual(state['allocations'][payments[-1].id]['excess'], D('0'))

    def test_overpayment_is_separate_from_debt(self):
        payment = pay(CHECK_DATE, '600000')
        state = get_debt_state(bank_debt(), as_of=CHECK_DATE, payments=statement() + [payment])
        self.assertEqual(state['total'], D('0'))
        self.assertEqual(state['allocations'][payment.id]['excess'], D('121262.49'))


class BankPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch('app.time_utils.utc_now', return_value=INSTANT)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.app = create_app(
            {
                'TESTING': True,
                'SECRET_KEY': 'bank-regression',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_ENGINE_OPTIONS': {},
                'WTF_CSRF_ENABLED': False,
            }
        )
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.user = User(telegram_id=5151001, timezone='Europe/Moscow')
        db.session.add(self.user)
        db.session.flush()
        self.debt = bank_debt(user_id=self.user.id)
        db.session.add(self.debt)
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user.id)
            session['_fresh'] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def record_statement(self):
        return [
            add_payment(
                self.debt,
                p.amount,
                payment_date=p.payment_date,
                is_early_repayment=p.is_early_repayment,
                scheduled_payment_amount=p.scheduled_payment_amount,
            )
            for p in statement()
        ]

    def test_persist_reload_recalculate_and_api_agree(self):
        payments = self.record_statement()
        recalculate_debt(self.debt)
        db.session.commit()
        db.session.expire_all()
        self.assertEqual(self.debt.opening_principal_amount, D('500000'))
        self.assertEqual(self.debt.remaining_amount, D('475517.02'))
        self.assertEqual(self.debt.total_debt(), D('478737.51'))
        payload = self.client.get(f'/api/debts/{self.debt.id}/payments').get_json()
        self.assertTrue(payload['debt']['calculation_valid'])
        self.assertEqual(payload['debt']['today'], '2026-11-05')
        self.assertEqual(payload['debt']['total_debt'], 478737.51)
        rows = {row['id']: row for row in payload['payments']}
        self.assertEqual(rows[payments[0].id]['principal_amount'], 0)
        self.assertEqual(rows[payments[2].id]['remaining_after_payment'], payload['debt']['current_principal'])
        schedule = build_debt_payment_schedule(self.debt, today=CHECK_DATE)
        self.assertEqual(schedule['rows'][0]['payment_date'], '2026-11-28')
        self.assertEqual(schedule['rows'][0]['remaining'], 471542.22)
        self.assertEqual(self.client.get('/api/calendar-date').get_json()['today'], '2026-11-05')

    def test_edit_and_delete_replay_without_changing_anchor(self):
        payments = self.record_statement()
        update_payment(self.debt, payments[1], D('25000'), date(2026, 9, 28))
        self.assertEqual(self.debt.opening_principal_amount, D('500000'))
        update_payment(self.debt, payments[1], D('25001.81'), date(2026, 9, 28))
        self.assertEqual(self.debt.remaining_amount, D('475517.02'))
        delete_payment(self.debt, payments[2])
        self.assertEqual(self.debt.remaining_amount, D('488120.11'))
        add_payment(self.debt, D('25000'), payment_date=date(2026, 10, 28))
        self.assertEqual(self.debt.remaining_amount, D('475517.02'))

    def test_explicit_reconciliation_preserves_payment_amounts(self):
        payments = self.record_statement()
        self.debt.opening_principal_amount = None
        self.debt.remaining_amount = D('450000')
        db.session.commit()
        amounts = [(p.id, p.amount) for p in payments]
        response = self.client.put(f'/api/debts/{self.debt.id}', json={'opening_principal_amount': '500000'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.debt.remaining_amount, D('475517.02'))
        self.assertEqual([(p.id, p.amount) for p in payments], amounts)

    def test_current_balance_edit_cannot_silently_rebase_payment_history(self):
        self.record_statement()
        response = self.client.put(f'/api/debts/{self.debt.id}', json={'remaining_amount': '475000'})
        self.assertEqual(response.status_code, 422, response.get_json())
        self.assertEqual(self.debt.opening_principal_amount, D('500000'))
        self.assertEqual(self.debt.remaining_amount, D('475517.02'))

    def test_overpayment_rolls_back_history(self):
        self.record_statement()
        with self.assertRaises(ValueError):
            add_payment(self.debt, D('600000'), payment_date=CHECK_DATE)
        self.assertEqual(Payment.query.filter_by(debt_id=self.debt.id).count(), 3)
        self.assertEqual(self.debt.remaining_amount, D('475517.02'))

    def test_conversion_to_split_allows_new_purchase_to_increase_principal(self):
        response = self.client.put(f'/api/debts/{self.debt.id}', json={'debt_type': 'split'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIsNone(self.debt.opening_principal_amount)
        response = self.client.post(
            f'/api/debts/{self.debt.id}/split-purchases',
            json={
                'title': 'Покупка',
                'amount': '1000',
                'purchase_date': CHECK_DATE.isoformat(),
                'installments_count': 4,
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()['debt']['current_principal'], 501000)

    def test_conversion_to_card_does_not_keep_fixed_loan_principal(self):
        response = self.client.put(
            f'/api/debts/{self.debt.id}',
            json={
                'debt_type': 'credit_card',
                'remaining_amount': '490000',
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIsNone(self.debt.opening_principal_amount)
        self.assertEqual(response.get_json()['debt']['current_principal'], 490000)

    def test_partial_early_payment_rebuilds_both_projection_strategies(self):
        debt = bank_debt(
            user_id=self.user.id,
            total_amount=D('100000'),
            remaining_amount=D('100000'),
            opening_principal_amount=D('100000'),
            interest_rate=D('0'),
            minimum_payment=D('10000'),
            first_payment_amount=None,
            next_payment_date=date(2026, 11, 28),
            accrual_start_date=date(2026, 10, 28),
            interest_period_start_date=date(2026, 10, 28),
            loan_term_months=12,
            early_repayment_strategy='reduce_term',
        )
        db.session.add(debt)
        db.session.commit()
        payment = add_payment(debt, D('10000'), payment_date=date(2026, 11, 1), is_early_repayment=True)
        self.assertEqual(payment.principal_amount, D('10000'))
        self.assertEqual(debt.remaining_amount, D('90000'))
        self.assertEqual(debt.next_payment_date, date(2026, 11, 28))
        term = build_debt_payment_schedule(debt, today=CHECK_DATE)
        debt.early_repayment_strategy = 'reduce_payment'
        reduced = build_debt_payment_schedule(debt, today=CHECK_DATE)
        self.assertEqual((len(term['rows']), term['rows'][0]['payment']), (9, 10000))
        self.assertEqual((len(reduced['rows']), reduced['rows'][0]['payment']), (12, 7500))


if __name__ == '__main__':
    unittest.main()
