"""The history accordion opens the month of the record that was just saved."""

import re
import unittest
from datetime import date
from urllib.parse import parse_qs, urlparse

from dateutil.relativedelta import relativedelta

from app import create_app
from app.models import Expense, Income, User
from app.services.history_month_service import month_key, parse_month_param, resolve_active_month
from extensions import db


def _groups(*months):
    return [{'year_month': month} for month in months]


class HistoryMonthServiceTestCase(unittest.TestCase):
    def test_month_key_matches_history_grouping(self):
        self.assertEqual(month_key(date(2026, 11, 5)), '2026-11')

    def test_parse_month_param_accepts_only_real_months(self):
        self.assertEqual(parse_month_param('2026-11'), '2026-11')
        self.assertEqual(parse_month_param(' 2026-01 '), '2026-01')
        for value in (None, '', '2026-13', '2026-00', '0000-05', '2026-1', '2026-11-05',
                      '11.2026', '2026-11<script>', 'abcd-ef'):
            self.assertIsNone(parse_month_param(value), value)

    def test_requested_month_wins_when_it_exists(self):
        groups = _groups('2026-11', '2026-10')
        self.assertEqual(resolve_active_month(groups, '2026-11', today=date(2026, 10, 15)), '2026-11')

    def test_unknown_or_invalid_request_falls_back_to_today(self):
        groups = _groups('2026-11', '2026-10')
        today = date(2026, 10, 15)
        self.assertEqual(resolve_active_month(groups, '2025-01', today=today), '2026-10')
        self.assertEqual(resolve_active_month(groups, 'bogus', today=today), '2026-10')
        self.assertEqual(resolve_active_month(groups, None, today=today), '2026-10')

    def test_falls_back_to_newest_group_without_current_month(self):
        groups = _groups('2026-12', '2026-11')
        self.assertEqual(resolve_active_month(groups, today=date(2026, 10, 15)), '2026-12')

    def test_empty_history_keeps_current_month(self):
        self.assertEqual(resolve_active_month([], '2026-11', today=date(2026, 10, 15)), '2026-10')


class HistoryMonthRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'history-month-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        self.today = date.today()
        self.future = self.today.replace(day=5) + relativedelta(months=1)
        with self.app.app_context():
            db.create_all()
            user = User(telegram_id=424242, username='history', first_name='History', role='user')
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
            db.session.add(Expense(user_id=user.id, amount=100, category='products',
                                   title='Текущий месяц', expense_date=self.today))
            db.session.add(Income(user_id=user.id, amount=1000, category='salary',
                                  source='Текущий месяц', income_date=self.today))
            db.session.commit()
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True
            session['telegram_mini_app'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _expanded_month(self, html):
        expanded = re.findall(r'id="collapse-(\d{4}-\d{2})" class="accordion-collapse collapse show"', html)
        self.assertEqual(len(expanded), 1, expanded)
        return expanded[0]

    def _redirect_month(self, response):
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlparse(response.headers['Location']).query).get('month', [None])[0]

    def test_future_expense_is_saved_and_its_month_is_expanded(self):
        response = self.client.post('/expenses', data={
            'category': 'products', 'title': 'Запланированная трата',
            'amount': '1500', 'expense_date': self.future.isoformat(),
        })

        self.assertEqual(self._redirect_month(response), month_key(self.future))
        with self.app.app_context():
            saved = Expense.query.filter_by(title='Запланированная трата').one()
            self.assertEqual(saved.expense_date, self.future)

        html = self.client.get(response.headers['Location']).get_data(as_text=True)
        self.assertEqual(self._expanded_month(html), month_key(self.future))
        self.assertIn('Расход сохранён', html)
        self.assertIn('Запланированная трата', html)

    def test_edited_expense_redirects_to_its_new_month(self):
        with self.app.app_context():
            expense_id = Expense.query.filter_by(title='Текущий месяц').one().id

        edit_page = self.client.get(f'/expenses/edit/{expense_id}').get_data(as_text=True)
        self.assertEqual(self._expanded_month(edit_page), month_key(self.today))

        response = self.client.post(f'/expenses/edit/{expense_id}', data={
            'category': 'products', 'title': 'Текущий месяц',
            'amount': '100', 'expense_date': self.future.isoformat(),
        })

        self.assertEqual(self._redirect_month(response), month_key(self.future))
        html = self.client.get(response.headers['Location']).get_data(as_text=True)
        self.assertEqual(self._expanded_month(html), month_key(self.future))

    def test_edit_page_expands_month_of_the_edited_expense(self):
        with self.app.app_context():
            expense = Expense(user_id=self.user_id, amount=50, category='products',
                              title='Будущая', expense_date=self.future)
            db.session.add(expense)
            db.session.commit()
            expense_id = expense.id

        html = self.client.get(f'/expenses/edit/{expense_id}').get_data(as_text=True)

        self.assertEqual(self._expanded_month(html), month_key(self.future))

    def test_invalid_month_parameter_is_ignored(self):
        html = self.client.get('/expenses?month=2026-13').get_data(as_text=True)
        self.assertEqual(self._expanded_month(html), month_key(self.today))

        html = self.client.get('/expenses?month=1999-01').get_data(as_text=True)
        self.assertEqual(self._expanded_month(html), month_key(self.today))

    def test_failed_save_does_not_change_expanded_month(self):
        response = self.client.post('/expenses', data={
            'category': 'products', 'title': '', 'amount': '10',
            'expense_date': self.future.isoformat(),
        })

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Название расхода обязательно', html)
        self.assertEqual(self._expanded_month(html), month_key(self.today))

    def test_future_income_is_saved_and_its_month_is_expanded(self):
        response = self.client.post('/incomes', data={
            'category': 'salary', 'source': 'Будущая премия',
            'amount': '5000', 'income_date': self.future.isoformat(),
        })

        self.assertEqual(self._redirect_month(response), month_key(self.future))
        with self.app.app_context():
            self.assertEqual(Income.query.filter_by(source='Будущая премия').one().income_date, self.future)
        html = self.client.get(response.headers['Location']).get_data(as_text=True)
        self.assertEqual(self._expanded_month(html), month_key(self.future))

    def test_edited_income_redirects_to_its_new_month(self):
        with self.app.app_context():
            income_id = Income.query.filter_by(source='Текущий месяц').one().id

        response = self.client.post(f'/incomes/edit/{income_id}', data={
            'category': 'salary', 'source': 'Текущий месяц',
            'amount': '1000', 'income_date': self.future.isoformat(),
        })

        self.assertEqual(self._redirect_month(response), month_key(self.future))
        html = self.client.get(response.headers['Location']).get_data(as_text=True)
        self.assertEqual(self._expanded_month(html), month_key(self.future))


class MiniAppRequestFailureTestCase(unittest.TestCase):
    """What a Mini App user sees when the session or CSRF token is lost."""

    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'history-month-csrf-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': True,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            user = User(telegram_id=434343, username='csrf', role='user')
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
        self.form = {'category': 'products', 'title': 'CSRF', 'amount': '10',
                     'expense_date': '2026-11-05'}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _assert_visible_400_and_not_saved(self, response):
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Некорректный запрос', html)
        with self.app.app_context():
            self.assertEqual(Expense.query.count(), 0)

    def test_invalid_csrf_token_shows_error_page(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True
            session['telegram_mini_app'] = True

        response = self.client.post('/expenses', data={**self.form, 'csrf_token': 'invalid'})

        self._assert_visible_400_and_not_saved(response)

    def test_post_without_session_cookie_shows_error_page(self):
        response = self.client.post('/expenses', data={**self.form, 'csrf_token': 'from-another-session'})

        self._assert_visible_400_and_not_saved(response)


if __name__ == '__main__':
    unittest.main()
