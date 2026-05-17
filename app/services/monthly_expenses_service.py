"""Service for managing monthly (recurring) expenses."""

from datetime import date
import uuid

from dateutil.relativedelta import relativedelta
from flask import current_app
from sqlalchemy import or_

from app.models import Expense
from extensions import db


def _stats(user_id=None, target_month=None):
    return {
        'created': 0,
        'skipped': 0,
        'errors': 0,
        'user_id': user_id,
        'target_month': target_month,
    }


def _today():
    configured = None
    try:
        configured = current_app.config.get('MONTHLY_EXPENSES_TODAY')
    except RuntimeError:
        configured = None

    if isinstance(configured, date):
        return configured
    if isinstance(configured, str) and configured:
        return date.fromisoformat(configured)
    return date.today()


def _month_key(value):
    if isinstance(value, date):
        return value.strftime('%Y-%m')
    return str(value)


def _month_start(month):
    year, month_number = map(int, _month_key(month).split('-'))
    return date(year, month_number, 1)


def _next_month(month_start):
    return month_start + relativedelta(months=1)


def _iter_months(start_month, end_month):
    current = _month_start(start_month)
    end = _month_start(end_month)
    while current <= end:
        yield current.strftime('%Y-%m')
        current = _next_month(current)


def _date_in_month(original_day, month):
    month_start = _month_start(month)
    last_day = (_next_month(month_start) - relativedelta(days=1)).day
    return date(month_start.year, month_start.month, min(original_day, last_day))


def _month_bounds(month):
    start = _month_start(month)
    return start, _next_month(start)


def _ensure_monthly_identity(expense):
    if not expense.monthly_group_id:
        expense.monthly_group_id = str(uuid.uuid4())
    if not expense.generated_for_month:
        expense.generated_for_month = _month_key(expense.expense_date)


def find_monthly_expense_for_month(user_id, monthly_group_id, month, exclude_expense_id=None):
    """Return any existing record for a monthly group in the given calendar month."""
    start, end = _month_bounds(month)
    query = Expense.query.filter(
        Expense.user_id == user_id,
        Expense.monthly_group_id == monthly_group_id,
        or_(
            Expense.generated_for_month == _month_key(month),
            (Expense.expense_date >= start) & (Expense.expense_date < end),
        ),
    )
    if exclude_expense_id is not None:
        query = query.filter(Expense.id != exclude_expense_id)
    return query.first()


def _create_monthly_copy(source_expense, target_month):
    return Expense(
        user_id=source_expense.user_id,
        amount=source_expense.amount,
        category=source_expense.category,
        title=source_expense.title,
        expense_date=_date_in_month(source_expense.expense_date.day, target_month),
        payment_method=source_expense.payment_method,
        comment=source_expense.comment,
        is_monthly=True,
        monthly_group_id=source_expense.monthly_group_id,
        generated_from_id=source_expense.id,
        generated_for_month=_month_key(target_month),
    )


def _pick_source_expense(expenses):
    roots = [expense for expense in expenses if expense.generated_from_id is None]
    candidates = roots or list(expenses)
    return sorted(candidates, key=lambda expense: (expense.expense_date, expense.id or 0))[0]


def generate_monthly_expenses_between(user_id, monthly_group_id, start_month, end_month, source_expense=None):
    """Create missing monthly copies for one group in an inclusive month range."""
    stats = _stats(user_id=user_id, target_month=_month_key(end_month))

    if source_expense is None:
        group_expenses = Expense.query.filter_by(
            user_id=user_id,
            monthly_group_id=monthly_group_id,
        ).all()
        if not group_expenses:
            return stats
        source_expense = _pick_source_expense(group_expenses)

    _ensure_monthly_identity(source_expense)
    source_month = _month_key(source_expense.expense_date)

    if _month_start(end_month) < _month_start(start_month):
        return stats

    for month in _iter_months(start_month, end_month):
        existing = find_monthly_expense_for_month(
            user_id,
            monthly_group_id,
            month,
            exclude_expense_id=source_expense.id if month != source_month else None,
        )
        if existing:
            stats['skipped'] += 1
            continue

        if month == source_month:
            stats['skipped'] += 1
            continue

        db.session.add(_create_monthly_copy(source_expense, month))
        stats['created'] += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        stats['errors'] += 1
        print(f"Error committing generated monthly expenses: {str(e)}")

    return stats


def generate_monthly_expenses_from_start_date(expense_id, end_month=None):
    """
    Generate all missing copies from the expense month to the current month.

    The source expense itself is kept in its original month; copies receive
    generated_from_id and generated_for_month.
    """
    source_expense = db.session.get(Expense, expense_id)
    if not source_expense or not source_expense.is_monthly:
        return _stats(target_month=_month_key(end_month or _today()))

    _ensure_monthly_identity(source_expense)
    db.session.flush()

    start_month = _month_key(source_expense.expense_date)
    end_month = _month_key(end_month or _today())
    return generate_monthly_expenses_between(
        source_expense.user_id,
        source_expense.monthly_group_id,
        start_month,
        end_month,
        source_expense=source_expense,
    )


def generate_monthly_expenses(user_id=None, target_month=None):
    """
    Generate monthly expenses up to the current or specified month.

    Existing groups are deduplicated in code by user_id and monthly_group_id,
    and each generated month is checked by generated_for_month and expense_date.
    """
    target_month = _month_key(target_month or _today())
    stats = _stats(user_id=user_id, target_month=target_month)

    query = Expense.query.filter(
        Expense.is_monthly == True,  # noqa: E712
        Expense.monthly_group_id.isnot(None),
    )
    if user_id is not None:
        query = query.filter(Expense.user_id == user_id)

    grouped = {}
    for expense in query.order_by(Expense.user_id.asc(), Expense.expense_date.asc(), Expense.id.asc()).all():
        grouped.setdefault((expense.user_id, expense.monthly_group_id), []).append(expense)

    for (group_user_id, monthly_group_id), group_expenses in grouped.items():
        source_expense = _pick_source_expense(group_expenses)
        start_month = _month_key(source_expense.expense_date)
        if _month_start(start_month) > _month_start(target_month):
            stats['skipped'] += 1
            continue

        group_stats = generate_monthly_expenses_between(
            group_user_id,
            monthly_group_id,
            start_month,
            target_month,
            source_expense=source_expense,
        )
        stats['created'] += group_stats['created']
        stats['skipped'] += group_stats['skipped']
        stats['errors'] += group_stats['errors']

    return stats


def create_monthly_expenses_for_new_month():
    """Generate missing monthly expenses for the current month."""
    return generate_monthly_expenses(user_id=None, target_month=_today())
