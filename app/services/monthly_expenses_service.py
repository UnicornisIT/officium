"""Service for managing monthly (recurring) expenses."""

from datetime import date, datetime, timedelta, timezone
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta
from flask import current_app
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

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

    try:
        timezone_name = current_app.config.get('APP_TIMEZONE', 'UTC')
    except RuntimeError:
        timezone_name = 'Europe/Moscow'
    # Europe/Moscow has stayed at UTC+03:00 since 2014. Keeping this explicit
    # fallback also makes the default work on Windows hosts without IANA tzdata.
    if timezone_name == 'Europe/Moscow':
        return datetime.now(timezone(timedelta(hours=3))).date()
    try:
        return datetime.now(ZoneInfo(timezone_name)).date()
    except ZoneInfoNotFoundError:
        try:
            current_app.logger.error('Unknown APP_TIMEZONE %s; using the server date', timezone_name)
        except RuntimeError:
            pass
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


def _ensure_monthly_identity(expense, anchor_day=None):
    if not expense.monthly_group_id:
        expense.monthly_group_id = str(uuid.uuid4())
    if not expense.generated_for_month:
        expense.generated_for_month = _month_key(expense.expense_date)
    if not expense.monthly_anchor_day:
        expense.monthly_anchor_day = anchor_day or expense.expense_date.day


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


def _create_monthly_copy(settings_expense, origin_expense, target_month, anchor_day):
    return Expense(
        user_id=settings_expense.user_id,
        amount=settings_expense.amount,
        category=settings_expense.category,
        title=settings_expense.title,
        expense_date=_date_in_month(anchor_day, target_month),
        payment_method=settings_expense.payment_method,
        comment=settings_expense.comment,
        is_monthly=True,
        monthly_group_id=settings_expense.monthly_group_id,
        generated_from_id=origin_expense.id,
        generated_for_month=_month_key(target_month),
        monthly_anchor_day=anchor_day,
        monthly_settings_updated_at=settings_expense.monthly_settings_updated_at,
    )


def _pick_origin_expense(expenses):
    roots = [expense for expense in expenses if expense.generated_from_id is None]
    candidates = roots or list(expenses)
    return sorted(candidates, key=lambda expense: (expense.expense_date, expense.id or 0))[0]


def _pick_settings_expense(expenses):
    """Pick the explicitly edited snapshot used only for future occurrences."""
    configured = [expense for expense in expenses if expense.monthly_settings_updated_at]
    if not configured:
        # Legacy rows predate the explicit settings revision. The most recent
        # active occurrence best represents the user's current settings, while
        # the separate origin still supplies the original recurrence day.
        return max(expenses, key=lambda expense: (expense.expense_date, expense.id or 0))
    return max(
        configured,
        key=lambda expense: (
            expense.monthly_settings_updated_at,
            expense.expense_date,
            expense.id or 0,
        ),
    )


def _group_expenses(user_id, monthly_group_id):
    return Expense.query.filter_by(
        user_id=user_id,
        monthly_group_id=monthly_group_id,
    ).order_by(Expense.expense_date.asc(), Expense.id.asc()).all()


def generate_monthly_expenses_between(user_id, monthly_group_id, start_month, end_month, source_expense=None):
    """Create missing monthly copies for one group in an inclusive month range."""
    stats = _stats(user_id=user_id, target_month=_month_key(end_month))

    group_expenses = _group_expenses(user_id, monthly_group_id)
    active_expenses = [expense for expense in group_expenses if expense.is_monthly]
    if not active_expenses:
        return stats

    origin_expense = _pick_origin_expense(group_expenses)
    if source_expense is None or not source_expense.is_monthly:
        source_expense = _pick_settings_expense(active_expenses)

    anchor_day = source_expense.monthly_anchor_day or origin_expense.monthly_anchor_day or origin_expense.expense_date.day
    _ensure_monthly_identity(origin_expense, anchor_day=anchor_day)
    _ensure_monthly_identity(source_expense, anchor_day=anchor_day)
    source_month = _month_key(origin_expense.expense_date)

    if _month_start(end_month) < _month_start(start_month):
        return stats

    try:
        for month in _iter_months(start_month, end_month):
            if find_monthly_expense_for_month(user_id, monthly_group_id, month):
                stats['skipped'] += 1
                continue

            if month == source_month:
                stats['skipped'] += 1
                continue

            try:
                # The savepoint lets a competing worker win one month without
                # rolling back other missing months in this series. The unique
                # index remains the final authority across processes.
                with db.session.begin_nested():
                    db.session.add(_create_monthly_copy(
                        source_expense,
                        origin_expense,
                        month,
                        anchor_day,
                    ))
                    db.session.flush()
                stats['created'] += 1
            except IntegrityError:
                stats['skipped'] += 1
                try:
                    current_app.logger.info(
                        'Monthly expense occurrence was already created concurrently '
                        'for user %s group %s month %s',
                        user_id,
                        monthly_group_id,
                        month,
                    )
                except RuntimeError:
                    pass

        db.session.commit()
    except Exception:
        db.session.rollback()
        stats['created'] = 0
        stats['errors'] += 1
        try:
            current_app.logger.exception('Failed to generate monthly expenses')
        except RuntimeError:
            pass

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

    group_expenses = _group_expenses(source_expense.user_id, source_expense.monthly_group_id)
    origin_expense = _pick_origin_expense(group_expenses)
    start_month = _month_key(origin_expense.expense_date)
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

    for (group_user_id, monthly_group_id), active_expenses in grouped.items():
        group_expenses = _group_expenses(group_user_id, monthly_group_id)
        origin_expense = _pick_origin_expense(group_expenses)
        source_expense = _pick_settings_expense(active_expenses)
        start_month = _month_key(origin_expense.expense_date)
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
