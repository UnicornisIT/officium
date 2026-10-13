"""Every screen shows the same total debt and overdue status.

Overdue debt: 100000 at 18%, 5000 due on 15.08 unpaid, penalty 20% a year.
On 25.08 (see tests/test_debt_accrual.py): overdue 5000 = 1528.77 interest +
3471.23 principal, penalty 5000 * 20% * 10 / 365 = 27.40, current interest
100000 * 18% * 10 / 365 = 493.15, total 100000 + 1528.77 + 493.15 + 27.40
= 102049.32.

Current debt: 50000 at 12%, next payment 05.09, accruals since 05.08:
50000 * 12% * 20 / 365 = 328.77, total 50328.77.
"""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app
from app.models import Debt, User
from app.services.finance_summary_service import get_finance_summary
from app.services.financial_plan_service import build_financial_plan
from app.services.telegram_bot_service import build_debt_reminder_message
from extensions import db


TODAY = date(2026, 8, 25)


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 25)


class DebtTotalsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'debt-totals-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        self.patches = [
            patch(f'{module}.date', FrozenDate)
            for module in (
                'app.models',
                'app.routes.main',
                'app.services.finance_summary_service',
                'app.services.financial_plan_service',
            )
        ]
        for date_patch in self.patches:
            date_patch.start()

        with self.app.app_context():
            db.create_all()
            user = User(telegram_id=1301, username='totals', first_name='Totals', role='user')
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
            overdue = Debt(
                user_id=user.id, status='active', bank_name='Overdue Bank', debt_type='consumer_credit',
                product_name='Late loan', total_amount=Decimal('120000.00'),
                remaining_amount=Decimal('100000.00'), minimum_payment=Decimal('5000.00'),
                interest_rate=Decimal('18.00'), next_payment_date=date(2026, 8, 15),
                accrual_start_date=date(2026, 7, 15), is_payment_recurring=True,
                day_count_convention='actual_year', penalty_rate=Decimal('20.00'),
            )
            current = Debt(
                user_id=user.id, status='active', bank_name='Calm Bank', debt_type='mortgage',
                product_name='Home', total_amount=Decimal('60000.00'),
                remaining_amount=Decimal('50000.00'), minimum_payment=Decimal('3000.00'),
                interest_rate=Decimal('12.00'), next_payment_date=date(2026, 9, 5),
                accrual_start_date=date(2026, 8, 5), is_payment_recurring=True,
                day_count_convention='actual_year', penalty_rate=Decimal('20.00'),
            )
            db.session.add_all([overdue, current])
            db.session.commit()
            self.overdue_id = overdue.id
            self.current_id = current.id

    def tearDown(self):
        for date_patch in self.patches:
            date_patch.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

    def test_api_returns_total_and_overdue(self):
        self.login()

        debt = self.client.get(f'/api/debts/{self.overdue_id}').get_json()['debt']

        self.assertEqual(debt['remaining_amount'], 100000.0)
        self.assertEqual(debt['total_debt'], 102049.32)
        self.assertEqual(debt['accrued_interest'], 2021.92)
        self.assertEqual(debt['penalty'], 27.4)
        self.assertTrue(debt['is_overdue'])
        self.assertEqual(debt['overdue_days'], 10)
        self.assertEqual(debt['amount_to_clear_overdue'], 5027.4)
        self.assertEqual(debt['effective_next_payment_date'], '2026-08-15')

        calm = self.client.get(f'/api/debts/{self.current_id}').get_json()['debt']
        self.assertFalse(calm['is_overdue'])
        self.assertEqual(calm['total_debt'], 50328.77)
        self.assertEqual(calm['effective_next_payment_date'], '2026-09-05')

    def test_summary_totals_include_interest_and_penalty(self):
        with self.app.app_context():
            summary = get_finance_summary(self.user_id)

        self.assertEqual(summary['total_remaining'], Decimal('152378.09'))
        self.assertEqual(summary['total_principal'], Decimal('150000.00'))
        self.assertEqual(summary['overdue_count'], 1)
        self.assertEqual(summary['total_mortgage_remaining'], Decimal('50328.77'))
        self.assertEqual(summary['nearest_debt'].id, self.current_id)

    def test_dashboard_and_mortgage_page_show_the_same_numbers(self):
        self.login()

        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('152\xa0378,09\xa0₽', html)
        self.assertIn('102\xa0049,32\xa0₽', html)
        self.assertIn('к оплате 5\xa0027,40\xa0₽', html)
        self.assertIn('У вас 1 просроченный платеж', html)

        mortgages = self.client.get('/mortgages').get_data(as_text=True)
        self.assertIn('50\xa0328,77\xa0₽', mortgages)
        self.assertNotIn('debt-card--overdue', mortgages)

    def test_telegram_reminder_uses_the_same_state(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            message = build_debt_reminder_message(user, today=TODAY, days=14)

        self.assertIn('Overdue Bank Late loan: просрочен на 10 дн., к оплате 5 027,40 руб.', message)
        self.assertIn('неустойка 27,40 руб.', message)
        self.assertIn('задолженность 102 049,32 руб.', message)
        self.assertIn('Calm Bank Home: через 11 дн., платеж 3 000 руб., задолженность 50 328,77 руб.', message)

    def test_financial_plan_uses_total_and_reserves_the_penalty(self):
        with self.app.app_context():
            plan = build_financial_plan(self.user_id, today=TODAY)

        items = {item['id']: item for item in plan['debt_items']}
        late = items[self.overdue_id]
        self.assertEqual(late['remaining'], 102049.32)
        self.assertEqual(late['principal'], 100000.0)
        self.assertTrue(late['is_overdue'])
        self.assertEqual(late['overdue_amount'], 5000.0)
        self.assertEqual(late['penalty'], 27.4)
        self.assertEqual(late['planned_payments'][0], {'amount': 27.4, 'due_date': TODAY})
        self.assertFalse(items[self.current_id]['is_overdue'])
        self.assertEqual(items[self.current_id]['remaining'], 50328.77)


if __name__ == '__main__':
    unittest.main()
