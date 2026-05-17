"""Service for managing monthly (recurring) expenses."""

from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from app.models import Expense, User
from extensions import db


def generate_monthly_expenses(user_id=None, target_month=None):
    """
    Generate monthly expenses for the current or specified month.
    
    Args:
        user_id: If provided, generate expenses only for this user. 
                If None, generate for all users.
        target_month: Target month as string 'YYYY-MM' or date object.
                     If None, defaults to current month.
    
    Returns:
        dict with statistics about generation
    """
    if target_month is None:
        target_month = date.today().strftime('%Y-%m')
    elif isinstance(target_month, date):
        target_month = target_month.strftime('%Y-%m')
    
    # Parse target month
    year, month = map(int, target_month.split('-'))
    target_date = date(year, month, 1)
    
    # Get the last day of target month
    last_day_of_target_month = (target_date + relativedelta(months=1, days=-1)).day
    
    # Get previous month
    prev_month_date = target_date - relativedelta(months=1)
    prev_month_str = prev_month_date.strftime('%Y-%m')
    
    stats = {
        'created': 0,
        'skipped': 0,
        'errors': 0,
        'user_id': user_id,
        'target_month': target_month,
    }
    
    # Build query for monthly expenses
    query = Expense.query.filter(
        Expense.is_monthly == True,
        Expense.generated_for_month == prev_month_str,
    )
    
    if user_id is not None:
        query = query.filter(Expense.user_id == user_id)
    
    monthly_expenses = query.all()
    
    for expense in monthly_expenses:
        try:
            # Check if expense already exists for this month
            existing = Expense.query.filter(
                Expense.monthly_group_id == expense.monthly_group_id,
                Expense.generated_for_month == target_month,
                Expense.user_id == expense.user_id,
            ).first()
            
            if existing:
                stats['skipped'] += 1
                continue
            
            # Calculate new date
            original_day = expense.expense_date.day
            try:
                new_date = date(year, month, original_day)
            except ValueError:
                # Day doesn't exist in target month (e.g., 31 Feb), use last day
                new_date = date(year, month, last_day_of_target_month)
            
            # Create new expense
            new_expense = Expense(
                user_id=expense.user_id,
                amount=expense.amount,
                category=expense.category,
                title=expense.title,
                expense_date=new_date,
                payment_method=expense.payment_method,
                comment=expense.comment,
                is_monthly=True,
                monthly_group_id=expense.monthly_group_id,
                generated_from_id=expense.id,
                generated_for_month=target_month,
            )
            
            db.session.add(new_expense)
            stats['created'] += 1
            
        except Exception as e:
            stats['errors'] += 1
            db.session.rollback()
            print(f"Error generating monthly expense for {expense.id}: {str(e)}")
    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        stats['errors'] += 1
        print(f"Error committing generated expenses: {str(e)}")
    
    return stats


def create_monthly_expenses_for_new_month():
    """
    Check if we need to create monthly expenses for the current month
    and create them if necessary.
    
    This is typically called on page load to ensure monthly expenses
    are generated without explicit user action.
    """
    current_month = date.today().strftime('%Y-%m')
    prev_month = (date.today() - relativedelta(months=1)).strftime('%Y-%m')
    
    # Check if we already have monthly expenses for this month
    existing_count = Expense.query.filter(
        Expense.generated_for_month == current_month,
    ).count()
    
    # If we already generated for this month, skip
    if existing_count > 0:
        return {'status': 'already_generated', 'count': existing_count}
    
    # Generate for all users
    stats = generate_monthly_expenses(user_id=None, target_month=current_month)
    return stats
