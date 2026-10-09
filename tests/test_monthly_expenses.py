"""Tests for monthly expenses functionality."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from dateutil.relativedelta import relativedelta
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.models import User, Expense
from app.services.monthly_expenses_service import (
    generate_monthly_expenses,
    generate_monthly_expenses_from_start_date,
)
from app.utils import group_entries_by_month
from extensions import db


class MonthlyExpensesTestCase(unittest.TestCase):
    """Test cases for monthly expenses."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'MONTHLY_EXPENSES_TODAY': date(2026, 5, 17),
        })
        self.client = self.app.test_client()
        
        with self.app.app_context():
            db.create_all()
            
            # Create test user
            self.user = User(
                telegram_id=12345,
                username='testuser',
                first_name='Test',
                last_name='User',
                role='user'
            )
            db.session.add(self.user)
            db.session.commit()
            
            self.user_id = self.user.id
    
    def tearDown(self):
        """Tear down test fixtures."""
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

    def post_expense(self, **overrides):
        self.login()
        data = {
            'amount': '1000',
            'category': 'products',
            'title': 'Backdated expense',
            'expense_date': '2026-03-15',
            'payment_method': 'card',
            'comment': '',
        }
        data.update(overrides)
        return self.client.post('/expenses', data=data, follow_redirects=False)

    def post_expense_edit(self, expense_id, **overrides):
        self.login()
        data = {
            'amount': '1000',
            'category': 'products',
            'title': 'Edited expense',
            'expense_date': '2026-03-15',
            'payment_method': 'card',
            'comment': '',
        }
        data.update(overrides)
        return self.client.post(f'/expenses/edit/{expense_id}', data=data, follow_redirects=False)

    def history_groups(self):
        expenses = Expense.query.filter_by(user_id=self.user_id).order_by(Expense.expense_date.desc()).all()
        return group_entries_by_month(expenses, 'expense_date')

    def group_by_month(self, year_month):
        return next((group for group in self.history_groups() if group['year_month'] == year_month), None)
    
    def test_regular_expense_created_without_monthly_flag(self):
        """Test that regular expenses are created without is_monthly flag."""
        with self.app.app_context():
            expense = Expense(
                user_id=self.user_id,
                amount=100.00,
                category='products',
                title='Groceries',
                expense_date=date.today(),
                is_monthly=False,
            )
            db.session.add(expense)
            db.session.commit()
            
            fetched = Expense.query.filter_by(id=expense.id).first()
            self.assertFalse(fetched.is_monthly)
            self.assertIsNone(fetched.monthly_group_id)

    def test_backdated_regular_expense_uses_expense_date_in_history(self):
        """Backdated regular expense appears in the month of expense_date."""
        with self.app.app_context():
            response = self.post_expense(
                amount='1250.50',
                title='March groceries',
                expense_date='2026-03-15',
            )

            self.assertEqual(response.status_code, 302)
            expense = Expense.query.filter_by(title='March groceries').one()
            self.assertEqual(expense.expense_date, date(2026, 3, 15))
            self.assertFalse(expense.is_monthly)

            march = self.group_by_month('2026-03')
            self.assertIsNotNone(march)
            self.assertEqual(len(march['items']), 1)
            self.assertEqual(march['total_amount'], Decimal('1250.50'))
            self.assertIsNone(self.group_by_month('2026-05'))
    
    def test_monthly_expense_has_monthly_group_id(self):
        """Test that monthly expenses get a monthly_group_id."""
        with self.app.app_context():
            expense = Expense(
                user_id=self.user_id,
                amount=100.00,
                category='rent',
                title='Monthly Rent',
                expense_date=date.today(),
                is_monthly=True,
                monthly_group_id='test-uuid-1234'
            )
            db.session.add(expense)
            db.session.commit()
            
            fetched = Expense.query.filter_by(id=expense.id).first()
            self.assertTrue(fetched.is_monthly)
            self.assertEqual(fetched.monthly_group_id, 'test-uuid-1234')

    def test_backdated_monthly_expense_creates_missing_copies_to_current_month(self):
        """Monthly expense created in May with a March date generates March-May records."""
        with self.app.app_context():
            response = self.post_expense(
                amount='1000',
                category='rent',
                title='Backdated rent',
                expense_date='2026-03-15',
                is_monthly='on',
            )

            self.assertEqual(response.status_code, 302)
            source = Expense.query.filter_by(title='Backdated rent', expense_date=date(2026, 3, 15)).one()
            records = Expense.query.filter_by(monthly_group_id=source.monthly_group_id).order_by(Expense.expense_date).all()

            self.assertEqual([expense.expense_date for expense in records], [
                date(2026, 3, 15),
                date(2026, 4, 15),
                date(2026, 5, 15),
            ])
            self.assertEqual({expense.monthly_group_id for expense in records}, {source.monthly_group_id})
            self.assertIsNone(source.generated_from_id)
            self.assertEqual(source.generated_for_month, '2026-03')
            for copy in records[1:]:
                self.assertEqual(copy.generated_from_id, source.id)
                self.assertEqual(copy.generated_for_month, copy.expense_date.strftime('%Y-%m'))

    def test_backdated_monthly_generation_is_idempotent(self):
        """Repeating generation does not create duplicates for existing months."""
        with self.app.app_context():
            self.post_expense(
                amount='1000',
                category='rent',
                title='Idempotent rent',
                expense_date='2026-03-15',
                is_monthly='on',
            )
            source = Expense.query.filter_by(title='Idempotent rent', expense_date=date(2026, 3, 15)).one()
            first_count = Expense.query.filter_by(monthly_group_id=source.monthly_group_id).count()

            generate_monthly_expenses_from_start_date(source.id, end_month='2026-05')
            second_count = Expense.query.filter_by(monthly_group_id=source.monthly_group_id).count()

            self.assertEqual(first_count, 3)
            self.assertEqual(second_count, 3)

    def test_backdated_monthly_expense_on_31st_uses_last_day_for_short_months(self):
        """A January 31 monthly expense creates valid dates through May."""
        with self.app.app_context():
            self.post_expense(
                amount='1000',
                category='subscriptions',
                title='Month-end subscription',
                expense_date='2026-01-31',
                is_monthly='on',
            )
            source = Expense.query.filter_by(title='Month-end subscription', expense_date=date(2026, 1, 31)).one()
            records = Expense.query.filter_by(monthly_group_id=source.monthly_group_id).order_by(Expense.expense_date).all()

            self.assertEqual([expense.expense_date for expense in records], [
                date(2026, 1, 31),
                date(2026, 2, 28),
                date(2026, 3, 31),
                date(2026, 4, 30),
                date(2026, 5, 31),
            ])
    
    def test_generate_monthly_expenses_creates_copy(self):
        """Test that generate_monthly_expenses creates copies for next month."""
        with self.app.app_context():
            # Create monthly expense for previous month
            prev_month = (date.today() - relativedelta(months=1))
            expense = Expense(
                user_id=self.user_id,
                amount=5000.00,
                category='rent',
                title='Rent',
                expense_date=date(prev_month.year, prev_month.month, 15),
                is_monthly=True,
                monthly_group_id='group-1',
                generated_for_month=prev_month.strftime('%Y-%m')
            )
            db.session.add(expense)
            db.session.commit()
            
            # Generate expenses for current month
            current_month = date.today().strftime('%Y-%m')
            stats = generate_monthly_expenses(target_month=current_month)
            
            # Check statistics
            self.assertGreaterEqual(stats['created'], 1)
            
            # Check that copy was created
            copies = Expense.query.filter(
                Expense.monthly_group_id == 'group-1',
                Expense.generated_for_month == current_month
            ).all()
            
            self.assertEqual(len(copies), 1)
            copy = copies[0]
            self.assertEqual(copy.amount, 5000.00)
            self.assertEqual(copy.title, 'Rent')
            self.assertEqual(copy.generated_from_id, expense.id)
    
    def test_generate_monthly_expenses_preserves_day_of_month(self):
        """Test that day of month is preserved when generating expenses."""
        with self.app.app_context():
            # Create expense on 15th of previous month
            prev_month = (date.today() - relativedelta(months=1))
            expense = Expense(
                user_id=self.user_id,
                amount=1000.00,
                category='rent',  # Changed from 'utilities' to valid category
                title='Rent',
                expense_date=date(prev_month.year, prev_month.month, 15),
                is_monthly=True,
                monthly_group_id='group-2',
                generated_for_month=prev_month.strftime('%Y-%m')
            )
            db.session.add(expense)
            db.session.commit()
            
            # Generate for current month
            current_month = date.today().strftime('%Y-%m')
            generate_monthly_expenses(target_month=current_month)
            
            # Check that copy is on 15th of current month
            copies = Expense.query.filter(
                Expense.monthly_group_id == 'group-2',
                Expense.generated_for_month == current_month
            ).all()
            
            self.assertEqual(len(copies), 1)
            copy = copies[0]
            self.assertEqual(copy.expense_date.day, 15)
    
    def test_generate_monthly_expenses_handles_invalid_day(self):
        """Test that expenses on 31st are handled correctly for months with fewer days."""
        with self.app.app_context():
            # Create expense on 31st of previous month
            prev_month = (date.today() - relativedelta(months=1))
            if prev_month.month == 12:
                prev_month = prev_month.replace(month=1, year=prev_month.year + 1)
            else:
                prev_month = prev_month.replace(month=prev_month.month + 1)
            
            # Actually use January 31st for safer testing
            expense = Expense(
                user_id=self.user_id,
                amount=2000.00,
                category='transport',
                title='Transport',
                expense_date=date(2026, 1, 31),
                is_monthly=True,
                monthly_group_id='group-3',
                generated_for_month='2026-01'
            )
            db.session.add(expense)
            db.session.commit()
            
            # Generate for February (28 days)
            generate_monthly_expenses(target_month='2026-02')
            
            # Check that copy was created on last day of February
            copies = Expense.query.filter(
                Expense.monthly_group_id == 'group-3',
                Expense.generated_for_month == '2026-02'
            ).all()
            
            self.assertEqual(len(copies), 1)
            copy = copies[0]
            self.assertEqual(copy.expense_date.day, 28)  # Last day of February
    
    def test_generate_monthly_expenses_no_duplicates(self):
        """Test that running generate twice doesn't create duplicates."""
        with self.app.app_context():
            # Create monthly expense
            prev_month = (date.today() - relativedelta(months=1))
            expense = Expense(
                user_id=self.user_id,
                amount=3000.00,
                category='subscriptions',
                title='Subscription',
                expense_date=date(prev_month.year, prev_month.month, 1),
                is_monthly=True,
                monthly_group_id='group-4',
                generated_for_month=prev_month.strftime('%Y-%m')
            )
            db.session.add(expense)
            db.session.commit()
            
            # Generate first time
            current_month = date.today().strftime('%Y-%m')
            stats1 = generate_monthly_expenses(target_month=current_month)
            count1 = stats1['created']
            
            # Generate second time
            stats2 = generate_monthly_expenses(target_month=current_month)
            count2 = stats2['skipped']
            
            # Should skip the already created expense
            self.assertEqual(count1, 1)
            self.assertGreaterEqual(count2, 1)
    
    def test_generate_monthly_expenses_for_specific_user(self):
        """Test that generate_monthly_expenses respects user_id filter."""
        with self.app.app_context():
            # Create second user
            user2 = User(
                telegram_id=54321,
                username='testuser2',
                first_name='Test',
                last_name='User2',
                role='user'
            )
            db.session.add(user2)
            db.session.commit()
            
            # Create expenses for both users in previous month
            prev_month = (date.today() - relativedelta(months=1))
            expense1 = Expense(
                user_id=self.user_id,
                amount=1000.00,
                category='products',
                title='User1 Expense',
                expense_date=date(prev_month.year, prev_month.month, 1),
                is_monthly=True,
                monthly_group_id='group-u1',
                generated_for_month=prev_month.strftime('%Y-%m')
            )
            expense2 = Expense(
                user_id=user2.id,
                amount=2000.00,
                category='products',
                title='User2 Expense',
                expense_date=date(prev_month.year, prev_month.month, 1),
                is_monthly=True,
                monthly_group_id='group-u2',
                generated_for_month=prev_month.strftime('%Y-%m')
            )
            db.session.add_all([expense1, expense2])
            db.session.commit()
            
            # Generate only for user1
            current_month = date.today().strftime('%Y-%m')
            stats = generate_monthly_expenses(user_id=self.user_id, target_month=current_month)
            
            # Only user1's expense should be generated
            user1_copies = Expense.query.filter(
                Expense.monthly_group_id == 'group-u1',
                Expense.user_id == self.user_id
            ).count()
            user2_copies = Expense.query.filter(
                Expense.monthly_group_id == 'group-u2'
            ).count()
            
            self.assertGreater(user1_copies, 1)  # Original + copy
            self.assertEqual(user2_copies, 1)    # Only original
    
    def test_generated_expense_preserves_all_fields(self):
        """Test that all fields are preserved when generating monthly expense."""
        with self.app.app_context():
            prev_month = (date.today() - relativedelta(months=1))
            expense = Expense(
                user_id=self.user_id,
                amount=5500.50,
                category='entertainment',
                title='Cinema & Events',
                expense_date=date(prev_month.year, prev_month.month, 10),
                payment_method='credit_card',
                comment='Monthly entertainment budget',
                is_monthly=True,
                monthly_group_id='group-5',
                generated_for_month=prev_month.strftime('%Y-%m')
            )
            db.session.add(expense)
            db.session.commit()
            
            # Generate
            current_month = date.today().strftime('%Y-%m')
            generate_monthly_expenses(target_month=current_month)
            
            # Check preserved fields
            copies = Expense.query.filter(
                Expense.monthly_group_id == 'group-5',
                Expense.generated_for_month == current_month
            ).all()
            
            self.assertEqual(len(copies), 1)
            copy = copies[0]
            self.assertEqual(copy.amount, 5500.50)
            self.assertEqual(copy.category, 'entertainment')
            self.assertEqual(copy.title, 'Cinema & Events')
            self.assertEqual(copy.payment_method, 'credit_card')
            self.assertEqual(copy.comment, 'Monthly entertainment budget')
            self.assertTrue(copy.is_monthly)
            self.assertEqual(copy.monthly_group_id, 'group-5')
            self.assertEqual(copy.generated_from_id, expense.id)

    def test_editing_regular_expense_date_moves_history_month(self):
        """Changing expense_date moves a regular expense between history months."""
        with self.app.app_context():
            expense = Expense(
                user_id=self.user_id,
                amount=750.00,
                category='products',
                title='Move me',
                expense_date=date(2026, 3, 15),
            )
            db.session.add(expense)
            db.session.commit()

            response = self.post_expense_edit(
                expense.id,
                amount='750',
                title='Move me',
                expense_date='2026-04-10',
            )

            self.assertEqual(response.status_code, 302)
            db.session.refresh(expense)
            self.assertEqual(expense.expense_date, date(2026, 4, 10))
            self.assertIsNone(self.group_by_month('2026-03'))
            april = self.group_by_month('2026-04')
            self.assertIsNotNone(april)
            self.assertEqual(april['total_amount'], Decimal('750.00'))

    def test_enabling_monthly_on_old_expense_creates_missing_copies(self):
        """Turning an old regular expense into monthly generates missing months."""
        with self.app.app_context():
            expense = Expense(
                user_id=self.user_id,
                amount=1500.00,
                category='rent',
                title='Old rent',
                expense_date=date(2026, 3, 15),
            )
            db.session.add(expense)
            db.session.commit()

            response = self.post_expense_edit(
                expense.id,
                amount='1500',
                category='rent',
                title='Old rent',
                expense_date='2026-03-15',
                is_monthly='on',
            )

            self.assertEqual(response.status_code, 302)
            db.session.refresh(expense)
            records = Expense.query.filter_by(monthly_group_id=expense.monthly_group_id).order_by(Expense.expense_date).all()
            self.assertEqual([item.expense_date for item in records], [
                date(2026, 3, 15),
                date(2026, 4, 15),
                date(2026, 5, 15),
            ])

    def test_disabling_monthly_stops_future_generation_without_deleting_history(self):
        """Turning off monthly prevents future copies and keeps existing records."""
        with self.app.app_context():
            self.post_expense(
                amount='1000',
                category='rent',
                title='Rent to stop',
                expense_date='2026-03-15',
                is_monthly='on',
            )
            source = Expense.query.filter_by(title='Rent to stop', expense_date=date(2026, 3, 15)).one()
            monthly_group_id = source.monthly_group_id
            initial_count = Expense.query.filter_by(monthly_group_id=monthly_group_id).count()

            response = self.post_expense_edit(
                source.id,
                amount='1000',
                category='rent',
                title='Rent to stop',
                expense_date='2026-03-15',
            )
            self.assertEqual(response.status_code, 302)

            generate_monthly_expenses(user_id=self.user_id, target_month='2026-06')
            records = Expense.query.filter_by(monthly_group_id=monthly_group_id).all()

            self.assertEqual(len(records), initial_count)
            self.assertFalse(any(record.expense_date.strftime('%Y-%m') == '2026-06' for record in records))
            self.assertTrue(all(not record.is_monthly for record in records))

    def test_september_to_october_and_year_rollover(self):
        with self.app.app_context():
            expense = Expense(
                user_id=self.user_id,
                amount=Decimal('680.00'),
                category='subscriptions',
                title='ChatGPT',
                expense_date=date(2026, 9, 19),
                is_monthly=True,
                monthly_group_id='calendar-rollover',
                generated_for_month='2026-09',
                monthly_anchor_day=19,
            )
            db.session.add(expense)
            db.session.commit()

            stats = generate_monthly_expenses(target_month='2027-01')
            records = Expense.query.filter_by(monthly_group_id='calendar-rollover').order_by(Expense.expense_date).all()

            self.assertEqual(stats['created'], 4)
            self.assertEqual([item.expense_date for item in records], [
                date(2026, 9, 19),
                date(2026, 10, 19),
                date(2026, 11, 19),
                date(2026, 12, 19),
                date(2027, 1, 19),
            ])

    def test_anchor_days_29_30_and_31_cover_leap_and_non_leap_february(self):
        with self.app.app_context():
            for day in (29, 30, 31):
                db.session.add(Expense(
                    user_id=self.user_id,
                    amount=day,
                    category='subscriptions',
                    title=f'Day {day}',
                    expense_date=date(2027, 1, day),
                    is_monthly=True,
                    monthly_group_id=f'anchor-{day}',
                    generated_for_month='2027-01',
                    monthly_anchor_day=day,
                ))
            db.session.commit()

            generate_monthly_expenses(target_month='2028-03')

            for day in (29, 30, 31):
                group_id = f'anchor-{day}'
                feb_2027 = Expense.query.filter_by(
                    monthly_group_id=group_id,
                    generated_for_month='2027-02',
                ).one()
                feb_2028 = Expense.query.filter_by(
                    monthly_group_id=group_id,
                    generated_for_month='2028-02',
                ).one()
                mar_2028 = Expense.query.filter_by(
                    monthly_group_id=group_id,
                    generated_for_month='2028-03',
                ).one()
                self.assertEqual(feb_2027.expense_date, date(2027, 2, 28))
                self.assertEqual(feb_2028.expense_date, date(2028, 2, 29))
                self.assertEqual(mar_2028.expense_date.day, day)

    def test_edit_updates_future_settings_without_rewriting_history(self):
        with self.app.app_context():
            self.post_expense(
                amount='680',
                category='subscriptions',
                title='ChatGPT',
                expense_date='2026-03-19',
                payment_method='card',
                comment='old settings',
                is_monthly='on',
            )
            may = Expense.query.filter_by(
                title='ChatGPT',
                generated_for_month='2026-05',
            ).one()
            march = Expense.query.filter_by(
                title='ChatGPT',
                generated_for_month='2026-03',
            ).one()

            response = self.post_expense_edit(
                may.id,
                amount='750.50',
                category='communication',
                title='ChatGPT Plus',
                expense_date='2026-05-31',
                payment_method='cash',
                comment='new settings',
                is_monthly='on',
            )
            self.assertEqual(response.status_code, 302)

            generate_monthly_expenses(target_month='2026-07')
            db.session.refresh(march)
            june = Expense.query.filter_by(
                monthly_group_id=march.monthly_group_id,
                generated_for_month='2026-06',
            ).one()
            july = Expense.query.filter_by(
                monthly_group_id=march.monthly_group_id,
                generated_for_month='2026-07',
            ).one()

            self.assertEqual(march.amount, Decimal('680.00'))
            self.assertEqual(march.title, 'ChatGPT')
            self.assertEqual(june.amount, Decimal('750.50'))
            self.assertEqual(june.category, 'communication')
            self.assertEqual(june.title, 'ChatGPT Plus')
            self.assertEqual(june.payment_method, 'cash')
            self.assertEqual(june.comment, 'new settings')
            self.assertEqual(june.expense_date, date(2026, 6, 30))
            self.assertEqual(july.expense_date, date(2026, 7, 31))

    def test_multiple_series_recover_several_missed_months(self):
        with self.app.app_context():
            db.session.add_all([
                Expense(
                    user_id=self.user_id,
                    amount=100,
                    category='rent',
                    title='Rent',
                    expense_date=date(2026, 1, 5),
                    is_monthly=True,
                    monthly_group_id='missed-rent',
                    generated_for_month='2026-01',
                ),
                Expense(
                    user_id=self.user_id,
                    amount=200,
                    category='subscriptions',
                    title='Service',
                    expense_date=date(2026, 2, 10),
                    is_monthly=True,
                    monthly_group_id='missed-service',
                    generated_for_month='2026-02',
                ),
            ])
            db.session.commit()

            first = generate_monthly_expenses(target_month='2026-05')
            second = generate_monthly_expenses(target_month='2026-05')

            self.assertEqual(first['created'], 7)
            self.assertEqual(second['created'], 0)
            self.assertEqual(Expense.query.filter_by(monthly_group_id='missed-rent').count(), 5)
            self.assertEqual(Expense.query.filter_by(monthly_group_id='missed-service').count(), 4)

    def test_legacy_series_uses_latest_values_but_original_anchor_day(self):
        with self.app.app_context():
            origin = Expense(
                user_id=self.user_id,
                amount=100,
                category='subscriptions',
                title='Legacy subscription',
                expense_date=date(2026, 1, 31),
                is_monthly=True,
                monthly_group_id='legacy-series',
                generated_for_month='2026-01',
            )
            db.session.add(origin)
            db.session.flush()
            february = Expense(
                user_id=self.user_id,
                amount=200,
                category='communication',
                title='Legacy edited subscription',
                expense_date=date(2026, 2, 28),
                is_monthly=True,
                monthly_group_id='legacy-series',
                generated_from_id=origin.id,
                generated_for_month='2026-02',
            )
            db.session.add(february)
            db.session.commit()

            generate_monthly_expenses(target_month='2026-03')
            march = Expense.query.filter_by(
                monthly_group_id='legacy-series',
                generated_for_month='2026-03',
            ).one()

            self.assertEqual(march.amount, Decimal('200.00'))
            self.assertEqual(march.category, 'communication')
            self.assertEqual(march.title, 'Legacy edited subscription')
            self.assertEqual(march.expense_date, date(2026, 3, 31))

    def test_database_constraint_rejects_parallel_duplicate_occurrence(self):
        with self.app.app_context():
            first = Expense(
                user_id=self.user_id,
                amount=100,
                category='rent',
                title='First worker',
                expense_date=date(2026, 10, 15),
                is_monthly=True,
                monthly_group_id='parallel-series',
                generated_for_month='2026-10',
            )
            second = Expense(
                user_id=self.user_id,
                amount=100,
                category='rent',
                title='Second worker',
                expense_date=date(2026, 10, 15),
                is_monthly=True,
                monthly_group_id='parallel-series',
                generated_for_month='2026-10',
            )
            db.session.add_all([first, second])

            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_concurrent_duplicate_does_not_rollback_other_missing_months(self):
        with self.app.app_context():
            origin = Expense(
                user_id=self.user_id,
                amount=100,
                category='rent',
                title='Concurrent rent',
                expense_date=date(2026, 1, 15),
                is_monthly=True,
                monthly_group_id='concurrent-recovery',
                generated_for_month='2026-01',
            )
            db.session.add(origin)
            db.session.flush()
            db.session.add(Expense(
                user_id=self.user_id,
                amount=100,
                category='rent',
                title='Concurrent rent',
                expense_date=date(2026, 2, 15),
                is_monthly=True,
                monthly_group_id='concurrent-recovery',
                generated_from_id=origin.id,
                generated_for_month='2026-02',
            ))
            db.session.commit()

            # Simulate a stale pre-insert read in a second worker. The unique
            # index rejects February inside a savepoint; March must still commit.
            with patch(
                'app.services.monthly_expenses_service.find_monthly_expense_for_month',
                return_value=None,
            ):
                stats = generate_monthly_expenses(target_month='2026-03')

            self.assertEqual(stats['created'], 1)
            self.assertEqual(stats['errors'], 0)
            self.assertEqual(Expense.query.filter_by(
                monthly_group_id='concurrent-recovery',
                generated_for_month='2026-02',
            ).count(), 1)
            self.assertEqual(Expense.query.filter_by(
                monthly_group_id='concurrent-recovery',
                generated_for_month='2026-03',
            ).count(), 1)


if __name__ == '__main__':
    unittest.main()
