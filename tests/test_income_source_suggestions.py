import unittest
from datetime import date
from decimal import Decimal

from app import create_app
from app.models import Income, User
from extensions import db


class IncomeSourceSuggestionsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            user = User(
                telegram_id=990,
                username='income_user',
                first_name='Income',
                role='user',
            )
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

    def test_income_source_field_suggests_previous_unique_sources(self):
        with self.app.app_context():
            db.session.add_all([
                Income(
                    user_id=self.user_id,
                    amount=Decimal('30000.00'),
                    category='salary',
                    source='СтГАУ',
                    income_date=date(2026, 7, 5),
                ),
                Income(
                    user_id=self.user_id,
                    amount=Decimal('10000.00'),
                    category='bonus',
                    source='СтГАУ',
                    income_date=date(2026, 6, 5),
                ),
                Income(
                    user_id=self.user_id,
                    amount=Decimal('15000.00'),
                    category='side_job',
                    source='Фриланс',
                    income_date=date(2026, 7, 1),
                ),
            ])
            db.session.commit()

        self.login()
        response = self.client.get('/incomes')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('list="incomeSourceSuggestions"', html)
        self.assertEqual(html.count('<option value="СтГАУ">'), 1)
        self.assertIn('<option value="Фриланс">', html)


if __name__ == '__main__':
    unittest.main()
