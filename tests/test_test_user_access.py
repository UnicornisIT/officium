import io
import re
import unittest

from app import create_app
from app.models import Expense, User
from extensions import db


class TestUserAccessTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'TEST_USER_ENABLED': True,
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def use_legacy_local_test_session(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = 'test-user'
            session['_fresh'] = True

    def test_legacy_test_user_session_can_save_expense(self):
        self.use_legacy_local_test_session()

        response = self.client.post('/expenses', data={
            'category': 'products',
            'title': 'MAGNIT',
            'amount': '1250,50',
            'expense_date': '2026-07-01',
            'payment_method': 'card',
            'comment': 'Тестовый расход',
        })

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            test_user = User.query.filter_by(telegram_id=-999999999999).one()
            expense = Expense.query.filter_by(user_id=test_user.id).one()
            self.assertEqual(expense.title, 'MAGNIT')

    def test_legacy_test_user_session_can_preview_statement_import(self):
        self.use_legacy_local_test_session()
        csv_text = (
            'Сбербанк Онлайн\n'
            'Дата операции;Описание операции;Сумма операции;Категория;Карта\n'
            '01.07.2026;PEREKRESTOK;-2100,00;Супермаркеты;*1234\n'
        )

        response = self.client.post(
            '/expenses/import',
            data={
                'statement_file': (
                    io.BytesIO(csv_text.encode('cp1251')),
                    'sber.csv',
                )
            },
            content_type='multipart/form-data',
        )

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('импорт выписки отключен', html.lower())
        self.assertIn('PEREKRESTOK', html)
        self.assertIsNotNone(re.search(r'name="import_token" value="([^"]+)"', html))


if __name__ == '__main__':
    unittest.main()
