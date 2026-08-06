import unittest
from datetime import date
from decimal import Decimal

from app import create_app
from app.models import Expense, User
from app.services.finance_summary_service import get_finance_summary
from extensions import db


class FinanceSummaryTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
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
