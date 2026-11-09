import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app
from app.models import Debt, Expense, Income, Payment, User
from app.services.finance_summary_service import get_finance_summary
from app.services.financial_plan_service import build_financial_plan
from extensions import db


class FinanceSummaryTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'finance-summary-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })

        with self.app.app_context():
            db.create_all()
            user = User(
                telegram_id=888,
                username='finance_user',
                first_name='Finance',
                role='user',
            )
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _debt_with_payment(self):
        debt = Debt(
            user_id=self.user_id, bank_name='Банк', product_name='Кредит',
            debt_type='consumer_credit', total_amount=Decimal('7200'),
            remaining_amount=Decimal('0'), minimum_payment=Decimal('7200'),
            interest_rate=Decimal('0'), status='active',
        )
        debt.payments.append(Payment(
            amount=Decimal('7302'), payment_date=date(2026, 11, 5),
            remaining_after_payment=Decimal('0'),
        ))
        db.session.add(debt)
        db.session.commit()
        return debt

    def test_archiving_preserves_closed_month_cashflow_and_restore_is_idempotent(self):
        with self.app.app_context(), patch('app.services.finance_summary_service.financial_today') as clock:
            clock.return_value = date(2026, 12, 7)
            debt = self._debt_with_payment()
            before = get_finance_summary(self.user_id, 2026, 11)
            self.assertEqual(before['total_payments'], Decimal('7302'))

            debt.status = 'archived'
            db.session.commit()
            after = get_finance_summary(self.user_id, 2026, 11)

            self.assertEqual(after['active_debts'], [])
            for key in ('total_payments', 'payments_this_month', 'free_balance', 'cashflow'):
                self.assertEqual(after[key], before[key])
            self.assertEqual(after['free_balance'], Decimal('-7302.00'))

            debt.status = 'active'
            db.session.commit()
            for _ in range(2):
                restored = get_finance_summary(self.user_id, 2026, 11)
                self.assertEqual(restored['total_payments'], before['total_payments'])
                self.assertEqual(restored['cashflow'], before['cashflow'])
                self.assertEqual(len(restored['payments_this_month']), 1)

    def test_deleting_archived_debt_does_not_change_displayed_month(self):
        with self.app.app_context(), patch('app.services.finance_summary_service.financial_today') as clock:
            clock.return_value = date(2026, 11, 7)
            db.session.add_all([
                Income(user_id=self.user_id, amount=Decimal('90000'),
                       category='salary', income_date=date(2026, 10, 5)),
                Expense(user_id=self.user_id, amount=Decimal('54150'),
                        category='other', title='Расходы', expense_date=date(2026, 10, 20)),
            ])
            debt = self._debt_with_payment()
            debt.status = 'archived'
            db.session.commit()
            income_count = Income.query.count()
            before = get_finance_summary(self.user_id)
            self.assertEqual(before['month_start'], date(2026, 11, 1))
            self.assertEqual(before['total_incomes'], Decimal('0'))

            db.session.delete(debt)
            db.session.commit()
            after = get_finance_summary(self.user_id)

            self.assertEqual(Income.query.count(), income_count)
            self.assertEqual(Payment.query.count(), 0)
            self.assertEqual(after['month_start'], date(2026, 11, 1))
            self.assertEqual(after['total_incomes'], Decimal('0'))
            self.assertEqual(after['total_expenses'], Decimal('0'))
            self.assertEqual(after['total_payments'], Decimal('0'))
            self.assertEqual(after['days_left'], 24)
            october = get_finance_summary(self.user_id, year=2026, month=10)
            self.assertEqual(october['total_incomes'], Decimal('90000'))
            self.assertEqual(october['total_expenses'], Decimal('54150'))

    def test_archive_removes_future_obligations_but_keeps_current_payments(self):
        with self.app.app_context(), patch('app.services.finance_summary_service.financial_today') as clock:
            clock.return_value = date(2026, 11, 7)
            debt = self._debt_with_payment()
            debt.remaining_amount = Decimal('7200.00')
            debt.minimum_payment = Decimal('600.00')
            debt.next_payment_date = date(2026, 12, 5)
            db.session.commit()
            before = build_financial_plan(self.user_id, today=date(2026, 12, 1))
            self.assertTrue(before['debt_items'])
            self.assertGreater(before['totals']['debt_payments'], 0)

            debt.status = 'archived'
            db.session.commit()
            current = get_finance_summary(self.user_id)
            future = get_finance_summary(self.user_id, 2026, 12)
            plan = build_financial_plan(self.user_id, today=date(2026, 12, 1))
            self.assertEqual(current['total_payments'], Decimal('7302.00'))
            self.assertEqual(current['active_debts'], [])
            self.assertEqual(future['active_debts'], [])
            self.assertEqual(future['total_remaining'], Decimal('0.00'))
            self.assertIsNone(future['nearest_debt'])
            self.assertEqual(plan['debt_items'], [])
            self.assertEqual(plan['totals']['debt_payments'], 0)

    def test_payment_month_boundaries_and_owner_isolation(self):
        with self.app.app_context():
            debt = self._debt_with_payment()
            debt.status = 'archived'
            for day, amount in ((date(2026, 10, 31), '1.01'),
                                (date(2026, 11, 1), '2.02'),
                                (date(2026, 11, 30), '3.03'),
                                (date(2026, 12, 1), '4.04')):
                debt.payments.append(Payment(
                    amount=Decimal(amount), payment_date=day,
                    remaining_after_payment=Decimal('0.00'),
                ))
            other = User(telegram_id=889, username='other_finance_user')
            db.session.add(other)
            db.session.flush()
            db.session.add(Debt(
                user_id=other.id, bank_name='Другой банк', product_name='Другой кредит',
                debt_type='consumer_credit', minimum_payment=Decimal('0.00'),
                total_amount=Decimal('99.99'), remaining_amount=Decimal('0.00'),
                status='archived', payments=[Payment(
                    amount=Decimal('99.99'), payment_date=date(2026, 11, 5),
                    remaining_after_payment=Decimal('0.00'),
                )],
            ))
            db.session.commit()
            for _ in range(2):
                summary = get_finance_summary(self.user_id, 2026, 11)
                self.assertEqual(summary['total_payments'], Decimal('7307.05'))
                self.assertEqual(len(summary['payments_this_month']), 3)
                self.assertEqual(summary['cashflow']['total_outflow'], Decimal('7307.05'))

    def test_linked_expense_payment_is_counted_once_across_archive_and_restore(self):
        with self.app.app_context(), patch('app.services.finance_summary_service.financial_today') as clock:
            clock.return_value = date(2026, 11, 7)
            debt = self._debt_with_payment()
            db.session.add(Expense(
                user_id=self.user_id, amount=Decimal('7302.00'), category='loans',
                title='Погашение', expense_date=date(2026, 11, 5),
                repayment_debt_id=debt.id, is_monthly=True,
            ))
            db.session.commit()
            for status in ('active', 'archived', 'active'):
                debt.status = status
                db.session.commit()
                summary = get_finance_summary(self.user_id, 2026, 11)
                self.assertEqual(summary['total_payments'], Decimal('7302.00'))
                self.assertEqual(summary['total_expenses'], Decimal('7302.00'))
                self.assertEqual(summary['free_balance'], Decimal('-7302.00'))
                flow = summary['cashflow']
                self.assertEqual(flow['payments_in_expenses'], Decimal('7302.00'))
                self.assertEqual(flow['payments_outside_expenses'], Decimal('0.00'))
                self.assertEqual(flow['total_outflow'], Decimal('7302.00'))
                self.assertEqual(flow['required_outflow'], Decimal('7302.00'))
                self.assertEqual(flow['projected_balance'], -flow['projected_expenses'])

    def test_overlap_requires_exact_link_date_amount_and_consumes_each_payment_once(self):
        cases = (
            (False, date(2026, 11, 5), '7302.00', 1, '0.00'),
            (True, date(2026, 11, 6), '7302.00', 1, '0.00'),
            (True, date(2026, 11, 5), '7302.01', 1, '0.00'),
            (True, date(2026, 11, 5), '7302.00', 2, '7302.00'),
        )
        with self.app.app_context():
            debt = self._debt_with_payment()
            for linked, day, amount, count, overlap in cases:
                with self.subTest(linked=linked, day=day, amount=amount, count=count):
                    expenses = [Expense(
                        user_id=self.user_id, amount=Decimal(amount), category='loans',
                        title='Погашение', expense_date=day,
                        repayment_debt_id=debt.id if linked else None,
                    ) for _ in range(count)]
                    db.session.add_all(expenses)
                    db.session.commit()
                    summary = get_finance_summary(self.user_id, 2026, 11)
                    expected = Decimal('7302.00') + Decimal(amount) * count - Decimal(overlap)
                    self.assertEqual(summary['cashflow']['payments_in_expenses'], Decimal(overlap))
                    self.assertEqual(summary['cashflow']['total_outflow'], expected)
                    self.assertEqual(summary['free_balance'], -expected)
                    self.assertEqual(summary['cashflow']['required_outflow'], Decimal('7302.00'))
                    self.assertEqual(
                        summary['cashflow']['required_outflow'] + summary['cashflow']['flexible_outflow'],
                        expected,
                    )
                    for expense in expenses:
                        db.session.delete(expense)
                    db.session.commit()

    def test_groups_expenses_by_clean_unique_title(self):
        with self.app.app_context():
            db.session.add_all([
                Expense(
                    user_id=self.user_id,
                    amount=Decimal('1166.00'),
                    category='transport',
                    title='Оплата в YANDEX*4121*GO Moskva RUS5500',
                    expense_date=date(2026, 7, 24),
                    payment_method='card',
                ),
                Expense(
                    user_id=self.user_id,
                    amount=Decimal('1337.00'),
                    category='transport',
                    title='Оплата в YANDEX*4121*GO Moskva RUS5500',
                    expense_date=date(2026, 7, 22),
                    payment_method='card',
                ),
                Expense(
                    user_id=self.user_id,
                    amount=Decimal('299.00'),
                    category='subscriptions',
                    title='MOSCOW OTO*Telegram. Операция по карте',
                    expense_date=date(2026, 7, 20),
                    payment_method='card',
                ),
            ])
            db.session.commit()

            summary = get_finance_summary(self.user_id, year=2026, month=7)

        titles = {item['label']: item for item in summary['expense_title_breakdown']}
        self.assertEqual(titles['YANDEX GO']['amount'], 2503.0)
        self.assertEqual(titles['YANDEX GO']['count'], 2)
        self.assertEqual(titles['YANDEX GO']['category_label'], 'Транспорт')
        self.assertEqual(titles['Telegram']['amount'], 299.0)


if __name__ == '__main__':
    unittest.main()
