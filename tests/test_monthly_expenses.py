"""Tests for monthly expenses functionality."""

import unittest
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

from app import create_app
from app.models import User, Expense
from app.services.monthly_expenses_service import generate_monthly_expenses
from extensions import db


class MonthlyExpensesTestCase(unittest.TestCase):
    """Test cases for monthly expenses."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
        })
        
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


if __name__ == '__main__':
    unittest.main()
