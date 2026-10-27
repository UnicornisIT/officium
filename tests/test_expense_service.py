"""Expense writes, monthly semantics, owner isolation and atomic audit records."""

import unittest
from datetime import date
from decimal import Decimal

from flask import g
from werkzeug.exceptions import NotFound

from app import create_app
from app.models import ActivityLog, Expense, User
from app.services.expense_service import create_expense, update_expense, delete_expense
from extensions import db


class ExpenseServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'expense-service-tests',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {}, 'WTF_CSRF_ENABLED': False,
            'MONTHLY_EXPENSES_TODAY': date(2026, 3, 15),
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        owner = User(telegram_id=10001, first_name='Owner', role='user')
        other = User(telegram_id=10002, first_name='Other', role='user')
        db.session.add_all([owner, other])
        db.session.commit()
        self.owner_id, self.other_id = owner.id, other.id
        self.values = dict(amount='1 250,255', category='products', title='  Fictional title  ',
                           expense_date='2026-01-31', payment_method=' card ', comment='  Fictional comment  ')

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def create(self, **overrides):
        return create_expense(user_id=self.owner_id, **(self.values | overrides))

    def update(self, expense, **overrides):
        return update_expense(user_id=self.owner_id, expense_id=expense.id, **(self.values | overrides))

    def test_rounding_normalization_and_optional_fields(self):
        expense = self.create()
        self.assertEqual(expense.amount, Decimal('1250.26'))
        self.assertEqual(expense.title, 'Fictional title')
        self.assertEqual(expense.comment, 'Fictional comment')
        self.assertEqual(expense.expense_date, date(2026, 1, 31))
        self.assertEqual(expense.payment_method, 'card')
        self.assertFalse(expense.is_monthly)
        self.assertIsNone(expense.monthly_group_id)
        self.update(expense, amount=Decimal('2.005'), payment_method='', comment=' ')
        self.assertEqual(expense.amount, Decimal('2.01'))
        self.assertIsNone(expense.payment_method)
        self.assertIsNone(expense.comment)

    def test_invalid_create_and_update_leave_data_and_log_unchanged(self):
        expense = self.create()
        for field, invalid in [
            ('amount', None), ('amount', ''), ('amount', '0'), ('amount', '-1'),
            ('amount', '0.001'), ('amount', 'NaN'), ('amount', 'Infinity'),
            ('amount', '10000000000'), ('amount', 'abc'), ('category', 'invalid'),
            ('payment_method', 'invalid'), ('expense_date', ''),
            ('expense_date', '2026-02-30'), ('expense_date', '31.01.2026'),
            ('title', ''), ('title', 'x' * 151), ('comment', 'x' * 2001),
        ]:
            for operation in (self.create, lambda **kw: self.update(expense, **kw)):
                with self.subTest(field=field, invalid=invalid, operation=operation):
                    with self.assertRaises(ValueError):
                        operation(**{field: invalid})
                    db.session.rollback()
                    self.assertEqual(Expense.query.count(), 1)
                    self.assertEqual(ActivityLog.query.count(), 1)
                    self.assertEqual(expense.amount, Decimal('1250.26'))

    def test_monthly_generation_clamps_day_and_retains_anchor(self):
        expense = self.create(is_monthly=True)
        group = Expense.query.filter_by(monthly_group_id=expense.monthly_group_id).order_by(Expense.expense_date).all()
        self.assertEqual([row.expense_date for row in group], [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)])
        self.assertEqual([row.generated_for_month for row in group], ['2026-01', '2026-02', '2026-03'])
        self.assertTrue(all(row.monthly_anchor_day == 31 for row in group))
        self.assertEqual(group[1].generated_from_id, expense.id)
        self.assertIsNotNone(expense.monthly_settings_updated_at)

    def test_enabling_and_disabling_monthly_preserves_group_history(self):
        expense = self.create()
        self.update(expense, is_monthly=True)
        group_id = expense.monthly_group_id
        self.update(expense, is_monthly=False)
        group = Expense.query.filter_by(monthly_group_id=group_id).all()
        self.assertEqual(len(group), 3)
        self.assertTrue(all(not row.is_monthly for row in group))
        self.assertEqual(expense.monthly_anchor_day, 31)
        self.assertEqual(expense.generated_for_month, '2026-01')

    def test_duplicate_month_rejected_without_changing_original(self):
        expense = self.create(is_monthly=True)
        with self.assertRaisesRegex(ValueError, 'уже есть запись'):
            self.update(expense, is_monthly=True, expense_date='2026-02-15')
        db.session.rollback()
        self.assertEqual(expense.expense_date, date(2026, 1, 31))
        self.assertEqual(ActivityLog.query.count(), 1)

    def test_edit_occurrence_keeps_historical_values(self):
        expense = self.create(is_monthly=True)
        february = Expense.query.filter_by(generated_for_month='2026-02').one()
        self.update(february, amount='500', is_monthly=True, expense_date='2026-02-20')
        self.assertEqual(february.monthly_anchor_day, 20)
        self.assertEqual(expense.amount, Decimal('1250.26'))
        self.assertEqual(Expense.query.filter_by(generated_for_month='2026-03').one().amount, Decimal('1250.26'))

    def test_delete_source_keeps_generated_occurrences(self):
        expense = self.create(is_monthly=True)
        expense_id = expense.id
        delete_expense(user_id=self.owner_id, expense_id=expense_id)
        self.assertIsNone(db.session.get(Expense, expense_id))
        self.assertEqual(Expense.query.count(), 2)
        self.assertTrue(all(row.generated_from_id is None for row in Expense.query.all()))

    def test_service_rejects_foreign_and_missing_ids(self):
        expense = self.create()
        for user_id, expense_id in [(self.other_id, expense.id), (self.owner_id, 99999)]:
            with self.assertRaises(NotFound):
                update_expense(user_id=user_id, expense_id=expense_id, **self.values)
            with self.assertRaises(NotFound):
                delete_expense(user_id=user_id, expense_id=expense_id)
        self.assertEqual(Expense.query.count(), 1)
        self.assertEqual(ActivityLog.query.count(), 1)

    def test_caller_transaction_rolls_back_expense_and_audit(self):
        self.create(commit=False)
        db.session.rollback()
        self.assertEqual(Expense.query.count(), 0)
        self.assertEqual(ActivityLog.query.count(), 0)
        expense = self.create()
        self.update(expense, amount='800', commit=False)
        db.session.rollback()
        self.assertEqual(expense.amount, Decimal('1250.26'))
        delete_expense(user_id=self.owner_id, expense_id=expense.id, commit=False)
        db.session.rollback()
        self.assertEqual(Expense.query.count(), 1)
        self.assertEqual(ActivityLog.query.count(), 1)

    def test_monthly_caller_transaction_and_unknown_options_rejected(self):
        with self.assertRaises(ValueError):
            self.create(is_monthly=True, commit=False)
        expense = self.create()
        with self.assertRaises(ValueError):
            self.update(expense, is_monthly=True, commit=False)
        with self.assertRaises(TypeError):
            self.create(unsupported=True)
        with self.assertRaises(TypeError):
            self.update(expense, unsupported=True)
        with self.assertRaises(TypeError):
            delete_expense(user_id=self.owner_id, expense_id=expense.id, unsupported=True)

    def test_audit_contains_only_action_and_category(self):
        expense = self.create(amount='45678', title='Secret title', comment='Secret comment')
        self.update(expense, amount='987654', title='Secret title', comment='Secret comment', category='transport')
        delete_expense(user_id=self.owner_id, expense_id=expense.id)
        logs = ActivityLog.query.order_by(ActivityLog.id).all()
        self.assertEqual([log.action for log in logs], ['expense_create', 'expense_update', 'expense_delete'])
        self.assertEqual([log.description for log in logs], ['products', 'transport', 'transport'])
        for log in logs:
            self.assertEqual(log.user_id, self.owner_id)
            self.assertEqual(log.entity_type, 'expense')
            self.assertEqual(log.entity_id, expense.id)

    def test_web_crud_and_idor(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(self.owner_id)
            session['_fresh'] = True
        response = client.post('/expenses', data=self.values)
        self.assertEqual(response.status_code, 302)
        self.assertIn('month=2026-01', response.location)
        expense = Expense.query.one()
        response = client.post(f'/expenses/edit/{expense.id}', data=self.values | dict(amount='600', expense_date='2026-04-15'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('month=2026-04', response.location)
        self.assertEqual(expense.amount, Decimal('600.00'))
        with client.session_transaction() as session:
            session['_user_id'] = str(self.other_id)
        g.pop('_login_user', None)
        self.assertEqual(client.get(f'/expenses/edit/{expense.id}').status_code, 404)
        self.assertEqual(client.post(f'/expenses/edit/{expense.id}', data=self.values).status_code, 404)
        self.assertEqual(client.post(f'/expenses/delete/{expense.id}').status_code, 404)
        with client.session_transaction() as session:
            session['_user_id'] = str(self.owner_id)
        g.pop('_login_user', None)
        self.assertEqual(client.post(f'/expenses/delete/{expense.id}').status_code, 302)
        self.assertEqual(Expense.query.count(), 0)

    def test_web_invalid_post_preserves_form_and_database(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(self.owner_id)
            session['_fresh'] = True
        response = client.post('/expenses', data=self.values | dict(amount='0'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Сумма расхода должна быть больше нуля', response.get_data(as_text=True))
        self.assertIn('Fictional title', response.get_data(as_text=True))
        self.assertEqual(Expense.query.count(), 0)
        self.assertEqual(ActivityLog.query.count(), 0)
