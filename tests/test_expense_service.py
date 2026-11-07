"""Expense writes, monthly semantics, owner isolation and atomic audit records."""

import unittest
from datetime import date
from decimal import Decimal

from flask import g
from werkzeug.exceptions import NotFound

from app import create_app
from app.models import ActivityLog, Expense, MonthlyExpenseStop, User
from app.services.expense_service import create_expense, update_expense, delete_expense, stop_expense_recurrence
from app.services.monthly_expenses_service import generate_monthly_expenses
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

    def test_delete_source_keeps_past_generated_occurrences(self):
        expense = self.create(is_monthly=True)
        expense_id = expense.id
        delete_expense(user_id=self.owner_id, expense_id=expense_id)
        self.assertIsNone(db.session.get(Expense, expense_id))
        self.assertEqual(Expense.query.count(), 1)
        self.assertEqual(Expense.query.one().expense_date, date(2026, 2, 28))
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

    def test_deleting_monthly_origin_or_copy_stops_generation_and_preserves_history(self):
        for index in (0, 1):
            with self.subTest(deleted_index=index):
                origin = self.create(is_monthly=True)
                group_id = origin.monthly_group_id
                rows = Expense.query.filter_by(monthly_group_id=group_id).order_by(Expense.expense_date).all()
                self.assertEqual(len(rows), 3)
                deleted_id = rows[index].id
                history = [(row.id, row.amount, row.expense_date) for row in rows
                           if row.id != deleted_id and row.expense_date <= date(2026, 3, 15)]

                other = self.create(is_monthly=True, title='Other series')
                foreign = Expense(user_id=self.other_id, amount=100, category='products',
                                  title='Other owner', expense_date=date(2026, 1, 31),
                                  is_monthly=True, monthly_group_id=group_id)
                db.session.add(foreign)
                db.session.commit()

                delete_expense(user_id=self.owner_id, expense_id=deleted_id)
                generate_monthly_expenses(user_id=self.owner_id, target_month='2026-06')
                generate_monthly_expenses(user_id=self.owner_id, target_month='2026-07')

                remaining = Expense.query.filter_by(user_id=self.owner_id, monthly_group_id=group_id).order_by(Expense.expense_date).all()
                self.assertEqual([(row.id, row.amount, row.expense_date) for row in remaining], history)
                self.assertTrue(all(row.is_monthly for row in remaining))
                self.assertIsNotNone(db.session.get(MonthlyExpenseStop, (self.owner_id, group_id)))
                self.assertTrue(foreign.is_monthly)
                self.assertTrue(other.is_monthly)
                self.assertEqual(Expense.query.filter_by(monthly_group_id=other.monthly_group_id).count(), 7)

    def test_monthly_delete_rollback_restores_series(self):
        origin = self.create(is_monthly=True)
        origin_id, group_id = origin.id, origin.monthly_group_id
        delete_expense(user_id=self.owner_id, expense_id=origin_id, commit=False)
        db.session.rollback()

        self.assertIsNotNone(db.session.get(Expense, origin_id))
        self.assertIsNone(db.session.get(MonthlyExpenseStop, (self.owner_id, group_id)))
        self.assertEqual(Expense.query.filter_by(monthly_group_id=group_id, is_monthly=True).count(), 3)
        generate_monthly_expenses(user_id=self.owner_id, target_month='2026-04')
        self.assertEqual(Expense.query.filter_by(monthly_group_id=group_id).count(), 4)

    def test_deleting_june_subscription_preserves_april_and_may_exactly(self):
        from app.services.financial_plan_service import build_financial_plan

        self.app.config['MONTHLY_EXPENSES_TODAY'] = date(2026, 6, 15)
        origin = self.create(is_monthly=True, expense_date='2026-04-15', category='subscriptions')
        group_id = origin.monthly_group_id
        rows = Expense.query.filter_by(monthly_group_id=group_id).order_by(Expense.expense_date).all()
        self.assertEqual(len(rows), 3)
        history = [row.to_dict() for row in rows[:2]]
        delete_expense(user_id=self.owner_id, expense_id=rows[2].id)

        for month in ('2026-06', '2026-07'):
            stats = generate_monthly_expenses(user_id=self.owner_id, target_month=month)
            self.assertEqual(stats['created'], 0)
        remaining = Expense.query.filter_by(monthly_group_id=group_id).order_by(Expense.expense_date).all()
        self.assertEqual([row.to_dict() for row in remaining], history)
        for month in (6, 7):
            plan = build_financial_plan(self.owner_id, today=date(2026, month, 15))
            self.assertEqual(plan['totals']['obligations'], 0)
        plan = build_financial_plan(self.owner_id, today=date(2026, 5, 15))
        self.assertEqual(plan['totals']['obligations'], 1250.26)

    def test_stop_repeat_preserves_paid_month_and_manual_future_expense(self):
        self.app.config['MONTHLY_EXPENSES_TODAY'] = date(2026, 6, 15)
        origin = self.create(is_monthly=True, expense_date='2026-04-15')
        group_id = origin.monthly_group_id
        generate_monthly_expenses(user_id=self.owner_id, target_month='2026-08')
        june = Expense.query.filter_by(monthly_group_id=group_id, generated_for_month='2026-06').one()
        august = Expense.query.filter_by(monthly_group_id=group_id, generated_for_month='2026-08').one()
        self.assertTrue(august.monthly_is_automatic)
        self.update(august, is_monthly=True, expense_date='2026-08-31', amount='2000')
        self.assertFalse(august.monthly_is_automatic)
        history = [row.to_dict() for row in Expense.query.filter(
            Expense.monthly_group_id == group_id, Expense.expense_date <= date(2026, 6, 15),
        ).order_by(Expense.expense_date).all()]
        august_id, june_id = august.id, june.id

        stop_expense_recurrence(user_id=self.owner_id, expense_id=june_id)
        self.assertFalse(june.monthly_is_automatic)
        self.assertEqual(db.session.get(MonthlyExpenseStop, (self.owner_id, group_id)).stopped_from, date(2026, 7, 1))
        self.assertIsNotNone(db.session.get(Expense, august_id))
        self.assertEqual(Expense.query.filter_by(monthly_group_id=group_id, generated_for_month='2026-07').count(), 0)
        self.assertEqual([row.to_dict() for row in Expense.query.filter(
            Expense.monthly_group_id == group_id, Expense.expense_date <= date(2026, 6, 15),
        ).order_by(Expense.expense_date).all()], history)
        self.update(june, is_monthly=True, expense_date='2026-06-15')
        generate_monthly_expenses(user_id=self.owner_id, target_month='2026-09')
        self.assertEqual(Expense.query.filter_by(monthly_group_id=group_id, generated_for_month='2026-09').count(), 0)
        new_series = self.create(is_monthly=True, expense_date='2026-09-15')
        self.assertNotEqual(new_series.monthly_group_id, group_id)
        generate_monthly_expenses(user_id=self.owner_id, target_month='2026-10')
        self.assertEqual(Expense.query.filter_by(monthly_group_id=new_series.monthly_group_id).count(), 2)

    def test_delete_removes_future_automatic_copies_only(self):
        self.app.config['MONTHLY_EXPENSES_TODAY'] = date(2026, 6, 15)
        origin = self.create(is_monthly=True, expense_date='2026-04-15')
        group_id = origin.monthly_group_id
        generate_monthly_expenses(user_id=self.owner_id, target_month='2026-08')
        june = Expense.query.filter_by(monthly_group_id=group_id, generated_for_month='2026-06').one()
        delete_expense(user_id=self.owner_id, expense_id=june.id)
        generate_monthly_expenses(user_id=self.owner_id, target_month='2026-09')
        self.assertEqual([row.generated_for_month for row in Expense.query.filter_by(
            monthly_group_id=group_id,
        ).order_by(Expense.expense_date).all()], ['2026-04', '2026-05'])

    def test_stop_repeat_web_confirms_month_and_checks_owner(self):
        origin = self.create(is_monthly=True, expense_date='2026-12-15')
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(self.other_id)
            session['_fresh'] = True
        self.assertEqual(client.post(f'/expenses/stop-repeat/{origin.id}').status_code, 404)
        with client.session_transaction() as session:
            session['_user_id'] = str(self.owner_id)
        g.pop('_login_user', None)
        page = client.get('/expenses').get_data(as_text=True)
        self.assertIn('Прекратить повторение с 01.2027?', page)
        self.assertIn('Удалить расход за 12.2026 и прекратить повторение с 12.2026?', page)
        self.assertEqual(client.post(f'/expenses/stop-repeat/{origin.id}').status_code, 302)
        self.assertIsNotNone(db.session.get(Expense, origin.id))
        page = client.get('/expenses').get_data(as_text=True)
        self.assertNotIn(f'/expenses/stop-repeat/{origin.id}', page)
        regular = self.create()
        self.assertIn('error=', client.post(f'/expenses/stop-repeat/{regular.id}').location)

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
