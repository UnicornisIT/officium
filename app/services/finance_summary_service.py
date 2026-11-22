from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func
from app.time_utils import financial_today
from app.models import User
from extensions import db
from app.models import Debt, Income, Expense, Payment
from app.services.financial_data import FinancialDataUnavailable, require_financial_data
from app.services.goal_service import empty_goal_transfers, month_transfers
from app.services.expense_title_service import clean_expense_title, expense_group_key
from app.services.mortgage_interest_service import mortgage_month_interest
from app.services.monthly_expenses_service import expected_monthly_expenses
from app.utils import EXPENSE_CATEGORIES, PAYMENT_METHODS


EXPENSE_CATEGORY_LABELS = dict(EXPENSE_CATEGORIES)
PAYMENT_METHOD_LABELS = dict(PAYMENT_METHODS)
MONEY = Decimal('0.01')
ZERO = Decimal('0.00')


def _money_value(value):
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _percent(part, total):
    if not total:
        return 0.0
    return float(
        ((part / total) * Decimal('100')).quantize(
            Decimal('0.1'), rounding=ROUND_HALF_UP
        )
    )


def _month_day_count(month_start, month_end):
    return max((month_end - month_start).days, 1)


def _elapsed_days(month_start, month_end, today):
    if month_start.year == today.year and month_start.month == today.month:
        last_counted_day = min(today, month_end - timedelta(days=1))
        return max((last_counted_day - month_start).days + 1, 1)
    if month_start > today:
        return 0
    return _month_day_count(month_start, month_end)


def _effective_debt_payment_date(debt, today):
    if hasattr(debt, 'effective_next_payment_date'):
        return debt.effective_next_payment_date(today)
    return debt.next_payment_date


def _debt_total(debt, today):
    """Principal plus unpaid interest and penalty."""
    if hasattr(debt, 'total_debt'):
        return _money_value(debt.total_debt(today))
    return _money_value(debt.remaining_amount)


def _is_overdue(debt, today):
    if hasattr(debt, 'is_overdue'):
        return debt.is_overdue(today)
    due_date = _effective_debt_payment_date(debt, today)
    return bool(due_date and due_date < today)


def _expense_view(expense):
    payment_method = getattr(expense, 'payment_method', None)
    title = getattr(expense, 'title', None) or 'Расход'
    clean_title = clean_expense_title(title)
    return {
        'id': getattr(expense, 'id', None),
        'title': title,
        'clean_title': clean_title,
        'amount': _money_value(getattr(expense, 'amount', 0)),
        'category': getattr(expense, 'category', None),
        'category_label': EXPENSE_CATEGORY_LABELS.get(
            getattr(expense, 'category', None), 'Другое'
        ),
        'expense_date': getattr(expense, 'expense_date', None),
        'payment_method_label': PAYMENT_METHOD_LABELS.get(payment_method, 'Не указан'),
        'is_monthly': _is_recurring(expense),
        'is_expected': bool(expense.monthly_is_automatic),
    }


def _build_category_breakdown(expenses, total_expenses):
    categories = {}
    for expense in expenses:
        key = getattr(expense, 'category', None) or 'other'
        if key not in categories:
            categories[key] = {
                'key': key,
                'label': EXPENSE_CATEGORY_LABELS.get(key, 'Другое'),
                'amount': ZERO,
                'count': 0,
                'monthly_amount': ZERO,
                'titles': {},
            }
        amount = _money_value(getattr(expense, 'amount', 0))
        title = clean_expense_title(getattr(expense, 'title', None) or '')
        title_key = expense_group_key(title)
        categories[key]['amount'] += amount
        categories[key]['count'] += 1
        if title_key not in categories[key]['titles']:
            categories[key]['titles'][title_key] = {'label': title, 'amount': ZERO}
        categories[key]['titles'][title_key]['amount'] += amount
        if getattr(expense, 'is_monthly', False):
            categories[key]['monthly_amount'] += amount

    breakdown = sorted(
        categories.values(), key=lambda item: item['amount'], reverse=True
    )
    for item in breakdown:
        item['percent'] = _percent(item['amount'], total_expenses)
        item['bar_percent'] = max(item['percent'], 2) if item['amount'] else 0
        item['top_titles'] = sorted(
            item.pop('titles').values(),
            key=lambda title: title['amount'],
            reverse=True,
        )[:3]
    return breakdown


def _build_payment_method_breakdown(expenses, total_expenses):
    methods = {}
    for expense in expenses:
        key = getattr(expense, 'payment_method', None) or 'unknown'
        if key not in methods:
            methods[key] = {
                'key': key,
                'label': PAYMENT_METHOD_LABELS.get(key, 'Не указан'),
                'amount': ZERO,
                'count': 0,
            }
        methods[key]['amount'] += _money_value(getattr(expense, 'amount', 0))
        methods[key]['count'] += 1

    breakdown = sorted(methods.values(), key=lambda item: item['amount'], reverse=True)
    for item in breakdown:
        item['percent'] = _percent(item['amount'], total_expenses)
    return breakdown


def _build_spending_days(expenses, total_expenses):
    days = {}
    for expense in expenses:
        expense_date = getattr(expense, 'expense_date', None)
        if not expense_date:
            continue
        key = expense_date.isoformat()
        if key not in days:
            days[key] = {'date': expense_date, 'amount': ZERO, 'count': 0}
        days[key]['amount'] += _money_value(getattr(expense, 'amount', 0))
        days[key]['count'] += 1

    top_days = sorted(days.values(), key=lambda item: item['amount'], reverse=True)[:3]
    for item in top_days:
        item['percent'] = _percent(item['amount'], total_expenses)
    return top_days


def _build_expense_title_breakdown(expense_items, total_expenses):
    titles = {}
    for expense in expense_items:
        title = expense['clean_title']
        key = expense_group_key(title)
        if key not in titles:
            titles[key] = {
                'key': key,
                'label': title,
                'amount': ZERO,
                'count': 0,
                'category_totals': {},
                'last_date': None,
            }

        amount = expense['amount']
        category_key = expense['category'] or 'other'
        titles[key]['amount'] += amount
        titles[key]['count'] += 1
        titles[key]['category_totals'][category_key] = (
            titles[key]['category_totals'].get(category_key, ZERO) + amount
        )
        expense_date = expense['expense_date']
        if expense_date and (
            titles[key]['last_date'] is None or expense_date > titles[key]['last_date']
        ):
            titles[key]['last_date'] = expense_date

    breakdown = sorted(titles.values(), key=lambda item: item['amount'], reverse=True)
    for item in breakdown:
        main_category = (
            max(item['category_totals'], key=item['category_totals'].get)
            if item['category_totals']
            else 'other'
        )
        item['category'] = main_category
        item['category_label'] = EXPENSE_CATEGORY_LABELS.get(main_category, 'Другое')
        item['percent'] = _percent(item['amount'], total_expenses)
        item['bar_percent'] = max(item['percent'], 2) if item['amount'] else 0
        item.pop('category_totals', None)
    return breakdown[:12]


def _is_recurring(expense):
    return bool(expense.is_monthly or expense.monthly_group_id)


def _payment_expense_overlap(expenses, unmatched):
    # Only an explicit debt link and an exact date/amount match identify the
    # same cash movement. Consume matches so repeated entries cannot reuse it.
    overlap = recurring_overlap = ZERO
    for expense in expenses:
        key = (
            expense.repayment_debt_id,
            expense.expense_date,
            _money_value(expense.amount),
        )
        if expense.category != 'loans' or key[0] is None or not unmatched[key]:
            continue
        unmatched[key] -= 1
        overlap += key[2]
        if _is_recurring(expense):
            recurring_overlap += key[2]
    return overlap, recurring_overlap


def _with_finance_insights(summary):
    expenses = summary.get('expenses_this_month', [])
    expected_expenses = summary.get('expected_expenses_this_month', [])
    total_expenses = _money_value(summary.get('total_expenses', 0))
    total_incomes = _money_value(summary.get('total_incomes', 0))
    total_payments = _money_value(summary.get('total_payments', 0))
    unmatched_payments = Counter(
        (payment.debt_id, payment.payment_date, _money_value(payment.amount))
        for payment in summary.get('payments_this_month', [])
    )
    overlap, recurring_overlap = _payment_expense_overlap(expenses, unmatched_payments)
    expected_overlap, _ = _payment_expense_overlap(
        expected_expenses, unmatched_payments
    )
    goal_transfers_net = _money_value(
        (summary.get('goal_transfers') or {}).get('net', 0)
    )
    total_outflow = total_expenses + total_payments - overlap
    free_balance = total_incomes - total_outflow - goal_transfers_net
    month_start = summary.get('month_start')
    month_end = summary.get('month_end')
    today = summary.get('today') or financial_today()

    expense_items = [_expense_view(expense) for expense in expenses]
    recurring_expenses = [item for item in expense_items if item['is_monthly']]
    recurring_expenses_total = sum(
        (item['amount'] for item in recurring_expenses), ZERO
    )
    expected_expenses_total = sum(
        (_money_value(expense.amount) for expense in expected_expenses), ZERO
    )
    one_time_expenses_total = max(
        total_expenses - recurring_expenses_total - (overlap - recurring_overlap), ZERO
    )
    required_outflow = recurring_expenses_total + total_payments - recurring_overlap

    month_days = (
        _month_day_count(month_start, month_end) if month_start and month_end else 0
    )
    elapsed_days = (
        _elapsed_days(month_start, month_end, today) if month_start and month_end else 0
    )
    is_current_month = bool(
        month_start
        and month_start.year == today.year
        and month_start.month == today.month
    )
    average_daily_expenses = total_expenses / elapsed_days if elapsed_days else ZERO
    projected_expenses = total_expenses
    projected_balance = free_balance
    balance_after_expected = free_balance
    if month_end and month_end > today:
        balance_after_expected -= expected_expenses_total - expected_overlap
        projected_expenses += expected_expenses_total
        if is_current_month:
            projected_expenses += _money_value(
                one_time_expenses_total * (month_days - elapsed_days) / elapsed_days
            )
        projected_balance = (
            total_incomes
            - projected_expenses
            - total_payments
            + overlap
            + expected_overlap
            - goal_transfers_net
        )

    summary.update(
        {
            'free_balance': free_balance,
            'balance_after_expected': balance_after_expected,
            'cashflow': {
                'total_outflow': total_outflow,
                'payments_in_expenses': overlap,
                'payments_outside_expenses': total_payments - overlap,
                'goal_transfers_net': goal_transfers_net,
                'recurring_expenses_total': recurring_expenses_total,
                'expected_expenses_total': expected_expenses_total,
                'one_time_expenses_total': one_time_expenses_total,
                'required_outflow': required_outflow,
                'flexible_outflow': one_time_expenses_total,
                'outflow_income_percent': _percent(total_outflow, total_incomes),
                'expenses_income_percent': _percent(total_expenses, total_incomes),
                'required_income_percent': _percent(required_outflow, total_incomes),
                'free_balance_percent': _percent(
                    max(free_balance, ZERO), total_incomes
                ),
                'average_daily_expenses': average_daily_expenses,
                'projected_expenses': projected_expenses,
                'projected_balance': projected_balance,
                'month_days': month_days,
                'elapsed_days': elapsed_days,
                'is_current_month': is_current_month,
            },
            'expense_category_breakdown': _build_category_breakdown(
                expenses, total_expenses
            ),
            'expense_title_breakdown': _build_expense_title_breakdown(
                expense_items, total_expenses
            ),
            'payment_method_breakdown': _build_payment_method_breakdown(
                expenses, total_expenses
            ),
            'largest_expenses': sorted(
                expense_items, key=lambda item: item['amount'], reverse=True
            )[:5],
            'recurring_expenses_this_month': sorted(
                recurring_expenses
                + [_expense_view(expense) for expense in expected_expenses],
                key=lambda item: (item['expense_date'] or date.max, item['title']),
            ),
            'top_spending_days': _build_spending_days(expenses, total_expenses),
        }
    )
    return summary


@require_financial_data()
def get_finance_summary(user_id, year=None, month=None, *, allow_partial=False):
    today = financial_today(db.session.get(User, user_id) if user_id else None)
    use_selected_month = year is not None and month is not None
    if use_selected_month:
        try:
            month_start = date(int(year), int(month), 1)
        except (TypeError, ValueError):
            month_start = date(today.year, today.month, 1)
    else:
        month_start = date(today.year, today.month, 1)
    month_end = (
        date(
            month_start.year + (month_start.month // 12),
            ((month_start.month % 12) + 1),
            1,
        )
        if month_start.month != 12
        else date(month_start.year + 1, 1, 1)
    )

    if user_id is None:

        def safe_day(day):
            return min(day, 28)

        debts = [
            Debt(
                id=101,
                user_id=0,
                bank_name='Тинькофф',
                debt_type='credit_card',
                product_name='Tinkoff Platinum',
                total_amount=85000,
                remaining_amount=47500,
                minimum_payment=3200,
                interest_rate=28.9,
                next_payment_date=date(
                    month_start.year, month_start.month, safe_day(25)
                ),
                comment='Основная кредитная карта',
                status='active',
            ),
            Debt(
                id=102,
                user_id=0,
                bank_name='Сбербанк',
                debt_type='split',
                product_name='СберСплит — MacBook Pro',
                total_amount=180000,
                remaining_amount=120000,
                minimum_payment=15000,
                interest_rate=None,
                next_payment_date=date(
                    month_start.year, month_start.month, safe_day(15)
                ),
                comment='12 платежей, прошло 4',
                status='active',
            ),
            Debt(
                id=103,
                user_id=0,
                bank_name='Альфа-Банк',
                debt_type='credit_card',
                product_name='Альфа-Карта',
                total_amount=50000,
                remaining_amount=8200,
                minimum_payment=1500,
                interest_rate=24.5,
                next_payment_date=date(
                    month_start.year, month_start.month, safe_day(5)
                ),
                comment='Почти погашена',
                status='active',
            ),
            Debt(
                id=104,
                user_id=0,
                bank_name='Сбербанк',
                debt_type='mortgage',
                product_name='Ипотека на квартиру',
                total_amount=3600000,
                remaining_amount=3480000,
                minimum_payment=22000,
                interest_rate=3.6,
                next_payment_date=date(
                    month_start.year, month_start.month, safe_day(10)
                ),
                comment='Ипотека на 20 лет',
                status='active',
            ),
            Debt(
                id=105,
                user_id=0,
                bank_name='Совкомбанк',
                debt_type='mortgage',
                product_name='Ипотека с просрочкой',
                total_amount=3600000,
                remaining_amount=3500000,
                minimum_payment=25000,
                interest_rate=14.0,
                next_payment_date=today - timedelta(days=12),
                comment='Просроченный платеж по ипотеке 14% годовых',
                status='active',
            ),
        ]

        incomes_this_month = [
            Income(
                id=201,
                user_id=0,
                amount=85000,
                category='salary',
                source='Основная работа',
                income_date=date(month_start.year, month_start.month, safe_day(10)),
                comment='Зарплата за текущий месяц',
            ),
            Income(
                id=202,
                user_id=0,
                amount=15000,
                category='bonus',
                source='Премия',
                income_date=date(month_start.year, month_start.month, safe_day(5)),
                comment='Премия за выполнение плана',
            ),
        ]

        expenses_this_month = [
            Expense(
                id=301,
                user_id=0,
                amount=3500,
                category='products',
                title='Продукты',
                expense_date=date(month_start.year, month_start.month, safe_day(12)),
                payment_method='card',
                comment='Покупка в супермаркете',
            ),
            Expense(
                id=302,
                user_id=0,
                amount=6200,
                category='transport',
                title='Транспорт',
                expense_date=date(month_start.year, month_start.month, safe_day(7)),
                payment_method='card',
                comment='Такси и метро',
            ),
            Expense(
                id=303,
                user_id=0,
                amount=2200,
                category='subscriptions',
                title='Подписки',
                expense_date=date(month_start.year, month_start.month, safe_day(3)),
                payment_method='card',
                comment='Онлайн-сервисы',
            ),
        ]

        payments_this_month = [
            Payment(
                id=401,
                debt_id=101,
                amount=10000,
                payment_date=date(month_start.year, month_start.month, safe_day(25)),
                comment='Плановый платеж',
                remaining_after_payment=57500,
            ),
            Payment(
                id=402,
                debt_id=101,
                amount=20000,
                payment_date=date(month_start.year, month_start.month, safe_day(5)),
                comment='Досрочный платеж',
                remaining_after_payment=67500,
            ),
        ]

        total_incomes = sum(
            (_money_value(item.amount) for item in incomes_this_month), ZERO
        )
        total_expenses = sum(
            (_money_value(item.amount) for item in expenses_this_month), ZERO
        )
        total_payments = sum(
            (_money_value(item.amount) for item in payments_this_month), ZERO
        )
        debts = sorted(
            debts, key=lambda d: _effective_debt_payment_date(d, today) or date.max
        )
        overdue_count = len([d for d in debts if _is_overdue(d, today)])
        nearest_debt = next(
            (
                d
                for d in debts
                if _effective_debt_payment_date(d, today)
                and _effective_debt_payment_date(d, today) >= today
            ),
            None,
        )

        mortgage_debts = [d for d in debts if d.debt_type == 'mortgage']
        total_mortgage_remaining = sum(
            (_debt_total(d, today) for d in mortgage_debts), ZERO
        )
        total_mortgage_original = sum(
            (_money_value(d.total_amount) for d in mortgage_debts), ZERO
        )
        total_mortgage_interest = mortgage_month_interest(
            mortgage_debts, month_start, month_end, today
        )

        return _with_finance_insights(
            {
                'today': today,
                'month_start': month_start,
                'month_end': month_end,
                'active_debts': debts,
                'mortgage_debts': mortgage_debts,
                'mortgage_count': len(mortgage_debts),
                'total_mortgage_remaining': total_mortgage_remaining,
                'total_mortgage_original': total_mortgage_original,
                'total_mortgage_interest': total_mortgage_interest,
                'mortgage_interest_is_estimate': month_start > today,
                'mortgage_interest_as_of': min(today, month_end),
                'total_remaining': sum((_debt_total(d, today) for d in debts), ZERO),
                'total_principal': sum(
                    (d.accrual_state(today)['principal'] for d in debts), ZERO
                ),
                'total_original': sum(
                    (_money_value(d.total_amount) for d in debts), ZERO
                ),
                'incomes_this_month': incomes_this_month,
                'expenses_this_month': expenses_this_month,
                'payments_this_month': payments_this_month,
                'total_incomes': total_incomes,
                'total_expenses': total_expenses,
                'total_payments': total_payments,
                'goal_transfers': empty_goal_transfers(),
                'days_left': (month_end - today).days
                if month_start.year == today.year and month_start.month == today.month
                else 0,
                'nearest_debt': nearest_debt,
                'overdue_count': overdue_count,
                'archived_count': 0,
                'total_debts': len(debts),
                'selected_year': month_start.year,
                'selected_month': month_start.month,
            }
        )

    period = {
        'today': today,
        'month_start': month_start,
        'month_end': month_end,
        'selected_year': month_start.year,
        'selected_month': month_start.month,
    }
    summary = dict(period)
    try:
        summary.update(_cashflow_summary(user_id, period))
    except FinancialDataUnavailable:
        if not allow_partial:
            raise
        summary['cashflow_error'] = FinancialDataUnavailable.code
    try:
        summary.update(_debt_summary(user_id, today, month_start, month_end))
    except FinancialDataUnavailable:
        if not allow_partial:
            raise
        summary['debt_error'] = FinancialDataUnavailable.code
    return summary


@require_financial_data()
def _cashflow_summary(user_id, period):
    month_start = period['month_start']
    month_end = period['month_end']
    today = period['today']
    incomes_this_month = (
        Income.query.filter_by(user_id=user_id)
        .filter(Income.income_date >= month_start, Income.income_date < month_end)
        .all()
    )
    month_expenses = (
        Expense.query.filter_by(user_id=user_id)
        .filter(Expense.expense_date >= month_start, Expense.expense_date < month_end)
        .all()
    )
    expenses_this_month = [
        expense for expense in month_expenses if not expense.monthly_is_automatic
    ]
    expected_expenses = expected_monthly_expenses(
        user_id, month_start, month_expenses, include_missing=month_end > today
    )
    # Archiving changes obligations, not the history of recorded payments.
    payments_this_month = (
        Payment.query.join(Debt)
        .filter(
            Debt.user_id == user_id,
            Payment.payment_date >= month_start,
            Payment.payment_date < month_end,
        )
        .all()
    )
    total_incomes = _money_value(
        Income.query.with_entities(func.coalesce(func.sum(Income.amount), 0))
        .filter_by(user_id=user_id)
        .filter(Income.income_date >= month_start, Income.income_date < month_end)
        .scalar()
    )
    total_expenses = sum(
        (_money_value(expense.amount) for expense in expenses_this_month), ZERO
    )
    total_payments = sum(
        (_money_value(payment.amount) for payment in payments_this_month), ZERO
    )
    # Transfers to goals reduce free cash without counting as expenses.
    goal_transfers = month_transfers(user_id, month_start, month_end)
    return _with_finance_insights(
        {
            **period,
            'incomes_this_month': incomes_this_month,
            'expenses_this_month': expenses_this_month,
            'expected_expenses_this_month': expected_expenses,
            'payments_this_month': payments_this_month,
            'total_incomes': total_incomes,
            'total_expenses': total_expenses,
            'total_payments': total_payments,
            'goal_transfers': goal_transfers,
            'days_left': (month_end - today).days
            if month_start <= today < month_end
            else 0,
        }
    )


@require_financial_data()
def _debt_summary(user_id, today, month_start, month_end):
    active_debts = (
        Debt.query.filter_by(status='active', user_id=user_id)
        .order_by(
            db.case((Debt.next_payment_date.is_(None), 1), else_=0),
            Debt.next_payment_date.asc(),
        )
        .all()
    )
    active_debts = sorted(
        active_debts, key=lambda d: _effective_debt_payment_date(d, today) or date.max
    )
    next_dates = [
        (debt, _effective_debt_payment_date(debt, today)) for debt in active_debts
    ]
    nearest_debt = next(
        (debt for debt, due_date in next_dates if due_date and due_date >= today), None
    )
    overdue_count = sum(_is_overdue(debt, today) for debt in active_debts)
    archived_count = Debt.query.filter_by(status='archived', user_id=user_id).count()
    total_debts = Debt.query.filter_by(user_id=user_id).count()
    mortgage_debts = [debt for debt in active_debts if debt.debt_type == 'mortgage']
    return {
        'active_debts': active_debts,
        'mortgage_debts': mortgage_debts,
        'mortgage_count': len(mortgage_debts),
        'total_mortgage_remaining': sum(
            (_debt_total(debt, today) for debt in mortgage_debts), ZERO
        ),
        'total_mortgage_original': sum(
            (_money_value(debt.total_amount) for debt in mortgage_debts), ZERO
        ),
        'total_mortgage_interest': mortgage_month_interest(
            mortgage_debts, month_start, month_end, today
        ),
        'mortgage_interest_is_estimate': month_start > today,
        'mortgage_interest_as_of': min(today, month_end),
        'total_remaining': sum(
            (_debt_total(debt, today) for debt in active_debts), ZERO
        ),
        'total_principal': sum(
            (debt.accrual_state(today)['principal'] for debt in active_debts), ZERO
        ),
        'total_original': sum(
            (_money_value(debt.total_amount) for debt in active_debts), ZERO
        ),
        'nearest_debt': nearest_debt,
        'overdue_count': overdue_count,
        'archived_count': archived_count,
        'total_debts': total_debts,
    }
