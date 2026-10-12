"""The admin panel must never show individual users' financial records."""

import unittest
from datetime import date

from app import create_app
from app.models import Debt, Expense, FinancialGoal, Income, Payment, User
from extensions import db


# Distinctive values seeded for one user; none of them may leak into admin pages.
SECRET_MARKERS = (
    'Приватный банк Омега',
    'Секретная карта',
    '987654',
    '987 654',
    '987\xa0654',
    '45678',
    '45 678',
    '45\xa0678',
    'Зарплата в конверте',
    'Тайная покупка',
    'Цель-невидимка',
)


class AdminPrivacyTestCase(unittest.TestCase):
    pages = (
        '/admin',
        '/admin/users',
        '/admin/logs',
        '/admin/system',
        '/admin/settings',
        '/admin/dictionaries',
        '/admin/server-update',
    )

    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'admin-privacy-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'SERVER_UPDATE_ENABLED': False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            superadmin = User(telegram_id=1, username='root', first_name='Корень', role='superadmin')
            admin = User(telegram_id=2, username='helper', first_name='Помощник', role='admin')
            owner = User(telegram_id=3, username='owner', first_name='Владелец', role='user')
            db.session.add_all([superadmin, admin, owner])
            db.session.flush()
            debt = Debt(
                user_id=owner.id,
                bank_name='Приватный банк Омега',
                debt_type='credit_card',
                product_name='Секретная карта',
                total_amount=987654,
                remaining_amount=45678,
                next_payment_date=date(2026, 11, 1),
                status='active',
            )
            db.session.add(debt)
            db.session.flush()
            db.session.add(Payment(
                debt_id=debt.id,
                amount=45678,
                payment_date=date(2026, 10, 1),
                remaining_after_payment=45678,
            ))
            db.session.add(Income(user_id=owner.id, amount=987654, category='salary', source='Зарплата в конверте', income_date=date(2026, 10, 1)))
            db.session.add(Expense(user_id=owner.id, amount=45678, category='other', title='Тайная покупка', expense_date=date(2026, 10, 2)))
            db.session.add(FinancialGoal(user_id=owner.id, name='Цель-невидимка', target_amount=987654))
            db.session.commit()
            self.user_ids = {'superadmin': superadmin.id, 'admin': admin.id, 'owner': owner.id}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def _assert_private(self, path):
        response = self.client.get(path)
        self.assertLess(response.status_code, 500, path)
        body = response.get_data(as_text=True)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, body, f'{path} leaks {marker!r}')

    def test_no_admin_page_shows_individual_financial_records(self):
        for role in ('superadmin', 'admin'):
            self._login(self.user_ids[role])
            paths = list(self.pages) + [f'/admin/users/{user_id}' for user_id in self.user_ids.values()]
            for path in paths:
                with self.subTest(role=role, path=path):
                    self._assert_private(path)

    def test_cross_user_finance_route_does_not_exist(self):
        self._login(self.user_ids['superadmin'])

        self.assertEqual(self.client.get('/admin/finance').status_code, 404)
        self.assertEqual(self.client.get('/admin/finance?section=payments').status_code, 404)


if __name__ == '__main__':
    unittest.main()
