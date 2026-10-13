"""The overdue page shows the accrual engine's numbers. Amounts are worked out
in tests/test_debt_accrual.py: 100000 at 18%, 5000 due on 15.08 unpaid,
penalty 20% a year."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app
from app.models import Debt, Payment, User
from extensions import db


NBSP = '\xa0'


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 25)


def rub(text):
    return text.replace(' ', NBSP) + NBSP + '₽'


class OverduePageTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'overdue-page-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        self.date_patch = patch('app.routes.main.date', FrozenDate)
        self.date_patch.start()

        with self.app.app_context():
            db.create_all()
            owner = User(telegram_id=1201, username='owner', first_name='Owner', role='user')
            stranger = User(telegram_id=1202, username='stranger', first_name='Stranger', role='user')
            db.session.add_all([owner, stranger])
            db.session.commit()
            self.user_id = owner.id
            self.stranger_id = stranger.id
            debt = Debt(
                user_id=owner.id,
                status='active',
                bank_name='Банк',
                debt_type='consumer_credit',
                product_name='Кредит',
                total_amount=Decimal('100000.00'),
                remaining_amount=Decimal('100000.00'),
                minimum_payment=Decimal('5000.00'),
                interest_rate=Decimal('18.00'),
                next_payment_date=date(2026, 8, 15),
                accrual_start_date=date(2026, 7, 15),
                is_payment_recurring=True,
                day_count_convention='actual_year',
                penalty_rate=Decimal('20.00'),
            )
            db.session.add(debt)
            db.session.commit()
            self.debt_id = debt.id

    def tearDown(self):
        self.date_patch.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, user_id=None):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id or self.user_id)
            session['_fresh'] = True

    def test_overdue_page_shows_real_breakdown_and_amount_to_clear(self):
        self.login()

        response = self.client.get(f'/debts/{self.debt_id}/overdue')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('демонстрацион', html)
        # 5000 overdue (1528.77 interest + 3471.23 principal) + penalty 27.40
        self.assertIn(f'внесите {rub("5 027,40")}', html)
        self.assertIn('10 дн. просрочки', html)
        self.assertIn(rub('1 528,77'), html)
        self.assertIn(rub('3 471,23'), html)
        self.assertIn(rub('27,40'), html)
        # Total: 100000 + 1528.77 + 493.15 current interest + 27.40 penalty
        self.assertIn(rub('102 049,32'), html)
        self.assertIn('Неоплаченные платежи', html)
        self.assertIn('15.08.2026', html)
        self.assertIn('по 24.08.2026 включительно', html)

    def test_page_without_overdue_says_so(self):
        self.login()
        with self.app.app_context():
            db.session.add(Payment(
                debt_id=self.debt_id,
                amount=Decimal('5000.00'),
                principal_amount=Decimal('0.00'),
                interest_amount=Decimal('0.00'),
                fee_amount=Decimal('0.00'),
                payment_date=date(2026, 8, 15),
                remaining_after_payment=Decimal('100000.00'),
            ))
            db.session.commit()

        response = self.client.get(f'/debts/{self.debt_id}/overdue')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Просрочки нет', html)
        self.assertIn('Следующий платёж 15.09.2026', html)
        self.assertNotIn('Неоплаченные платежи', html)

    def test_page_of_another_users_debt_is_not_found(self):
        self.login(self.stranger_id)

        response = self.client.get(f'/debts/{self.debt_id}/overdue')

        self.assertEqual(response.status_code, 404)
        html = response.get_data(as_text=True)
        self.assertIn('Долг не найден', html)
        self.assertNotIn('внесите', html)


if __name__ == '__main__':
    unittest.main()
