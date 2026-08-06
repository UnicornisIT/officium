from datetime import date, timedelta
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from extensions import db
from app.models import Debt, Income, Expense, Payment
from app.services.expense_title_service import clean_expense_title, expense_group_key
from app.utils import EXPENSE_CATEGORIES, PAYMENT_METHODS


EXPENSE_CATEGORY_LABELS = dict(EXPENSE_CATEGORIES)
PAYMENT_METHOD_LABELS = dict(PAYMENT_METHODS)


def _money_value(value):
    return float(value or 0)


def _percent(part, total):
    return round((part / total) * 100, 1) if total else 0


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
        'category_label': EXPENSE_CATEGORY_LABELS.get(getattr(expense, 'category', None), 'Другое'),
        'expense_date': getattr(expense, 'expense_date', None),
        'payment_method_label': PAYMENT_METHOD_LABELS.get(payment_method, 'Не указан'),
        'is_monthly': bool(getattr(expense, 'is_monthly', False)),
    }


def _build_category_breakdown(expenses, total_expenses):
    categories = {}
    for expense in expenses:
        key = getattr(expense, 'category', None) or 'other'
        if key not in categories:
            categories[key] = {
                'key': key,
                'label': EXPENSE_CATEGORY_LABELS.get(key, 'Другое'),
                'amount': 0.0,
                'count': 0,
                'monthly_amount': 0.0,
                'titles': {},
            }
        amount = _money_value(getattr(expense, 'amount', 0))
        title = clean_expense_title(getattr(expense, 'title', None) or '')
        title_key = expense_group_key(title)
        categories[key]['amount'] += amount
        categories[key]['count'] += 1
        if title_key not in categories[key]['titles']:
            categories[key]['titles'][title_key] = {'label': title, 'amount': 0.0}
        categories[key]['titles'][title_key]['amount'] += amount
        if getattr(expense, 'is_monthly', False):
            categories[key]['monthly_amount'] += amount

    breakdown = sorted(categories.values(), key=lambda item: item['amount'], reverse=True)
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
                'amount': 0.0,
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
            days[key] = {'date': expense_date, 'amount': 0.0, 'count': 0}
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
                'amount': 0.0,
                'count': 0,
                'category_totals': {},
                'last_date': None,
            }

        amount = expense['amount']
        category_key = expense['category'] or 'other'
        titles[key]['amount'] += amount
        titles[key]['count'] += 1
        titles[key]['category_totals'][category_key] = titles[key]['category_totals'].get(category_key, 0.0) + amount
        expense_date = expense['expense_date']
        if expense_date and (titles[key]['last_date'] is None or expense_date > titles[key]['last_date']):
            titles[key]['last_date'] = expense_date

    breakdown = sorted(titles.values(), key=lambda item: item['amount'], reverse=True)
    for item in breakdown:
        main_category = max(item['category_totals'], key=item['category_totals'].get) if item['category_totals'] else 'other'
        item['category'] = main_category
        item['category_label'] = EXPENSE_CATEGORY_LABELS.get(main_category, 'Другое')
        item['percent'] = _percent(item['amount'], total_expenses)
        item['bar_percent'] = max(item['percent'], 2) if item['amount'] else 0
        item.pop('category_totals', None)
    return breakdown[:12]


def _with_finance_insights(summary):
    expenses = summary.get('expenses_this_month', [])
    total_expenses = _money_value(summary.get('total_expenses', 0))
    total_incomes = _money_value(summary.get('total_incomes', 0))
    total_payments = _money_value(summary.get('total_payments', 0))
    free_balance = _money_value(summary.get('free_balance', 0))
    month_start = summary.get('month_start')
    month_end = summary.get('month_end')
    today = summary.get('today') or date.today()

    expense_items = [_expense_view(expense) for expense in expenses]
    recurring_expenses = [item for item in expense_items if item['is_monthly']]
    recurring_expenses_total = sum(item['amount'] for item in recurring_expenses)
    one_time_expenses_total = max(total_expenses - recurring_expenses_total, 0)
    total_outflow = total_expenses + total_payments
    required_outflow = recurring_expenses_total + total_payments

    month_days = _month_day_count(month_start, month_end) if month_start and month_end else 0
    elapsed_days = _elapsed_days(month_start, month_end, today) if month_start and month_end else 0
    is_current_month = bool(month_start and month_start.year == today.year and month_start.month == today.month)
    average_daily_expenses = total_expenses / elapsed_days if elapsed_days else 0
    projected_expenses = average_daily_expenses * month_days if is_current_month and elapsed_days else total_expenses
    projected_balance = total_incomes - projected_expenses - total_payments if is_current_month else free_balance

    summary.update({
        'cashflow': {
            'total_outflow': total_outflow,
            'recurring_expenses_total': recurring_expenses_total,
            'one_time_expenses_total': one_time_expenses_total,
            'required_outflow': required_outflow,
            'flexible_outflow': one_time_expenses_total,
            'outflow_income_percent': _percent(total_outflow, total_incomes),
            'expenses_income_percent': _percent(total_expenses, total_incomes),
            'required_income_percent': _percent(required_outflow, total_incomes),
            'free_balance_percent': _percent(max(free_balance, 0), total_incomes),
            'average_daily_expenses': average_daily_expenses,
            'projected_expenses': projected_expenses,
            'projected_balance': projected_balance,
            'month_days': month_days,
            'elapsed_days': elapsed_days,
            'is_current_month': is_current_month,
        },
        'expense_category_breakdown': _build_category_breakdown(expenses, total_expenses),
        'expense_title_breakdown': _build_expense_title_breakdown(expense_items, total_expenses),
        'payment_method_breakdown': _build_payment_method_breakdown(expenses, total_expenses),
        'largest_expenses': sorted(expense_items, key=lambda item: item['amount'], reverse=True)[:5],
        'recurring_expenses_this_month': sorted(
            recurring_expenses,
            key=lambda item: (item['expense_date'] or date.max, item['title']),
        ),
        'top_spending_days': _build_spending_days(expenses, total_expenses),
    })
    return summary


def get_finance_summary(user_id, year=None, month=None):
    today = date.today()
    use_selected_month = year is not None and month is not None
    if use_selected_month:
        try:
            month_start = date(int(year), int(month), 1)
        except (TypeError, ValueError):
            month_start = date(today.year, today.month, 1)
    else:
        month_start = date(today.year, today.month, 1)
    month_end = date(month_start.year + (month_start.month // 12), ((month_start.month % 12) + 1), 1) if month_start.month != 12 else date(month_start.year + 1, 1, 1)

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
                next_payment_date=date(month_start.year, month_start.month, safe_day(25)),
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
                next_payment_date=date(month_start.year, month_start.month, safe_day(15)),
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
                next_payment_date=date(month_start.year, month_start.month, safe_day(5)),
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
                next_payment_date=date(month_start.year, month_start.month, safe_day(10)),
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

        total_incomes = sum(float(item.amount) for item in incomes_this_month)
        total_expenses = sum(float(item.amount) for item in expenses_this_month)
        total_payments = sum(float(item.amount) for item in payments_this_month)
        free_balance = total_incomes - total_expenses - total_payments
        debts = sorted(debts, key=lambda d: _effective_debt_payment_date(d, today) or date.max)
        overdue_count = len([d for d in debts if _effective_debt_payment_date(d, today) and _effective_debt_payment_date(d, today) < today])
        nearest_debt = next((d for d in debts if _effective_debt_payment_date(d, today) and _effective_debt_payment_date(d, today) >= today), None)

        mortgage_debts = [d for d in debts if d.debt_type == 'mortgage']
        total_mortgage_remaining = sum(float(d.remaining_amount) for d in mortgage_debts)
        total_mortgage_original = sum(float(d.total_amount) for d in mortgage_debts)
        total_mortgage_interest = sum(
            float(d.remaining_amount) * float(d.interest_rate_for(month_start)) / 12 / 100
            for d in mortgage_debts
            if d.interest_rate_for(month_start) is not None
        )

        return _with_finance_insights({
            'today': today,
            'month_start': month_start,
            'month_end': month_end,
            'active_debts': debts,
            'mortgage_debts': mortgage_debts,
            'mortgage_count': len(mortgage_debts),
            'total_mortgage_remaining': total_mortgage_remaining,
            'total_mortgage_original': total_mortgage_original,
            'total_mortgage_interest': total_mortgage_interest,
            'total_remaining': sum(float(d.remaining_amount) for d in debts),
            'total_original': sum(float(d.total_amount) for d in debts),
            'incomes_this_month': incomes_this_month,
            'expenses_this_month': expenses_this_month,
            'payments_this_month': payments_this_month,
            'total_incomes': total_incomes,
            'total_expenses': total_expenses,
            'total_payments': total_payments,
            'free_balance': free_balance,
            'days_left': (month_end - today).days if month_start.year == today.year and month_start.month == today.month else 0,
            'nearest_debt': nearest_debt,
            'overdue_count': overdue_count,
            'archived_count': 0,
            'total_debts': len(debts),
            'selected_year': month_start.year,
            'selected_month': month_start.month,
        })

    try:
        active_debts = Debt.query.filter_by(status='active', user_id=user_id).order_by(db.case((Debt.next_payment_date.is_(None), 1), else_=0), Debt.next_payment_date.asc()).all()
        active_debts = sorted(active_debts, key=lambda d: _effective_debt_payment_date(d, today) or date.max)
        total_remaining = sum(float(d.remaining_amount) for d in active_debts)
        total_original = sum(float(d.total_amount) for d in active_debts)

        incomes_this_month = Income.query.filter_by(user_id=user_id).filter(Income.income_date >= month_start, Income.income_date < month_end).all()
        expenses_this_month = Expense.query.filter_by(user_id=user_id).filter(Expense.expense_date >= month_start, Expense.expense_date < month_end).all()
        payments_this_month = Payment.query.join(Debt).filter(Debt.user_id == user_id, Payment.payment_date >= month_start, Payment.payment_date < month_end).all()

        if not use_selected_month and not (incomes_this_month or expenses_this_month or payments_this_month):
            latest_income = Income.query.with_entities(func.max(Income.income_date)).filter_by(user_id=user_id).scalar()
            latest_expense = Expense.query.with_entities(func.max(Expense.expense_date)).filter_by(user_id=user_id).scalar()
            latest_payment = Payment.query.join(Debt).with_entities(func.max(Payment.payment_date)).filter(Debt.user_id == user_id).scalar()
            latest_date = max(d for d in (latest_income, latest_expense, latest_payment) if d is not None) if any((latest_income, latest_expense, latest_payment)) else None
            if latest_date:
                month_start = date(latest_date.year, latest_date.month, 1)
                month_end = date(month_start.year + (month_start.month // 12), ((month_start.month % 12) + 1), 1) if month_start.month != 12 else date(month_start.year + 1, 1, 1)
                incomes_this_month = Income.query.filter_by(user_id=user_id).filter(Income.income_date >= month_start, Income.income_date < month_end).all()
                expenses_this_month = Expense.query.filter_by(user_id=user_id).filter(Expense.expense_date >= month_start, Expense.expense_date < month_end).all()
                payments_this_month = Payment.query.join(Debt).filter(Debt.user_id == user_id, Payment.payment_date >= month_start, Payment.payment_date < month_end).all()

        total_incomes = float(Income.query.with_entities(func.coalesce(func.sum(Income.amount), 0)).filter_by(user_id=user_id).filter(Income.income_date >= month_start, Income.income_date < month_end).scalar() or 0)
        total_expenses = float(Expense.query.with_entities(func.coalesce(func.sum(Expense.amount), 0)).filter_by(user_id=user_id).filter(Expense.expense_date >= month_start, Expense.expense_date < month_end).scalar() or 0)
        total_payments = float(Payment.query.with_entities(func.coalesce(func.sum(Payment.amount), 0)).join(Debt).filter(Debt.user_id == user_id, Payment.payment_date >= month_start, Payment.payment_date < month_end).scalar() or 0)
        archived_count = Debt.query.filter_by(status='archived', user_id=user_id).count()
        total_debts = Debt.query.filter_by(user_id=user_id).count()
    except SQLAlchemyError:
        return _with_finance_insights({
            'today': today,
            'month_start': month_start,
            'month_end': month_end,
            'active_debts': [],
            'mortgage_debts': [],
            'mortgage_count': 0,
            'total_mortgage_remaining': 0.0,
            'total_mortgage_original': 0.0,
            'total_mortgage_interest': 0.0,
            'total_remaining': 0.0,
            'total_original': 0.0,
            'incomes_this_month': [],
            'expenses_this_month': [],
            'payments_this_month': [],
            'total_incomes': 0.0,
            'total_expenses': 0.0,
            'total_payments': 0.0,
            'free_balance': 0.0,
            'days_left': 0,
            'nearest_debt': None,
            'overdue_count': 0,
            'archived_count': 0,
            'total_debts': 0,
            'selected_year': month_start.year,
            'selected_month': month_start.month,
        })

    incomes_this_month = Income.query.filter_by(user_id=user_id).filter(Income.income_date >= month_start, Income.income_date < month_end).all()
    expenses_this_month = Expense.query.filter_by(user_id=user_id).filter(Expense.expense_date >= month_start, Expense.expense_date < month_end).all()
    payments_this_month = Payment.query.join(Debt).filter(Debt.user_id == user_id, Payment.payment_date >= month_start, Payment.payment_date < month_end).all()

    if not use_selected_month and not (incomes_this_month or expenses_this_month or payments_this_month):
        latest_income = Income.query.with_entities(func.max(Income.income_date)).filter_by(user_id=user_id).scalar()
        latest_expense = Expense.query.with_entities(func.max(Expense.expense_date)).filter_by(user_id=user_id).scalar()
        latest_payment = Payment.query.join(Debt).with_entities(func.max(Payment.payment_date)).filter(Debt.user_id == user_id).scalar()
        latest_date = max(d for d in (latest_income, latest_expense, latest_payment) if d is not None) if any((latest_income, latest_expense, latest_payment)) else None
        if latest_date:
            month_start = date(latest_date.year, latest_date.month, 1)
            month_end = date(month_start.year + (month_start.month // 12), ((month_start.month % 12) + 1), 1) if month_start.month != 12 else date(month_start.year + 1, 1, 1)
            incomes_this_month = Income.query.filter_by(user_id=user_id).filter(Income.income_date >= month_start, Income.income_date < month_end).all()
            expenses_this_month = Expense.query.filter_by(user_id=user_id).filter(Expense.expense_date >= month_start, Expense.expense_date < month_end).all()
            payments_this_month = Payment.query.join(Debt).filter(Debt.user_id == user_id, Payment.payment_date >= month_start, Payment.payment_date < month_end).all()

    total_incomes = float(Income.query.with_entities(func.coalesce(func.sum(Income.amount), 0)).filter_by(user_id=user_id).filter(Income.income_date >= month_start, Income.income_date < month_end).scalar() or 0)
    total_expenses = float(Expense.query.with_entities(func.coalesce(func.sum(Expense.amount), 0)).filter_by(user_id=user_id).filter(Expense.expense_date >= month_start, Expense.expense_date < month_end).scalar() or 0)
    total_payments = float(Payment.query.with_entities(func.coalesce(func.sum(Payment.amount), 0)).join(Debt).filter(Debt.user_id == user_id, Payment.payment_date >= month_start, Payment.payment_date < month_end).scalar() or 0)
    free_balance = total_incomes - total_expenses - total_payments
    active_debts = sorted(active_debts, key=lambda d: _effective_debt_payment_date(d, today) or date.max)
    overdue_count = len([d for d in active_debts if _effective_debt_payment_date(d, today) and _effective_debt_payment_date(d, today) < today])
    archived_count = Debt.query.filter_by(status='archived', user_id=user_id).count()
    total_debts = Debt.query.filter_by(user_id=user_id).count()

    if month_start.year == today.year and month_start.month == today.month:
        days_left = (month_end - today).days
    else:
        days_left = 0

    nearest_debt = next((d for d in active_debts if _effective_debt_payment_date(d, today) and _effective_debt_payment_date(d, today) >= today), None)

    mortgage_debts = [d for d in active_debts if d.debt_type == 'mortgage']
    total_mortgage_remaining = sum(float(d.remaining_amount) for d in mortgage_debts)
    total_mortgage_original = sum(float(d.total_amount) for d in mortgage_debts)
    total_mortgage_interest = sum(
        float(d.remaining_amount) * float(d.interest_rate_for(month_start)) / 12 / 100
        for d in mortgage_debts
        if d.interest_rate_for(month_start) is not None
    )

    return _with_finance_insights({
        'today': today,
        'month_start': month_start,
        'month_end': month_end,
        'active_debts': active_debts,
        'mortgage_debts': mortgage_debts,
        'mortgage_count': len(mortgage_debts),
        'total_mortgage_remaining': total_mortgage_remaining,
        'total_mortgage_original': total_mortgage_original,
        'total_mortgage_interest': total_mortgage_interest,
        'total_remaining': total_remaining,
        'total_original': total_original,
        'incomes_this_month': incomes_this_month,
        'expenses_this_month': expenses_this_month,
        'payments_this_month': payments_this_month,
        'total_incomes': total_incomes,
        'total_expenses': total_expenses,
        'total_payments': total_payments,
        'free_balance': free_balance,
        'days_left': days_left,
        'nearest_debt': nearest_debt,
        'overdue_count': overdue_count,
        'archived_count': archived_count,
        'total_debts': total_debts,
        'selected_year': month_start.year,
        'selected_month': month_start.month,
    })
