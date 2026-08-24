from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta
from sqlalchemy.exc import SQLAlchemyError

from app.models import (
    Debt,
    EmergencyFundTransaction,
    Expense,
    FinancialGoal,
    FinancialGoalTransaction,
    FinancialPlanPreference,
    Income,
)
from app.services.debt_schedule_service import build_debt_payment_schedule
from app.services.finance_summary_service import get_finance_summary
from app.services.payment_service import paid_toward_payment_cycle, principal_paid_in_payment_cycle
from app.utils import EXPENSE_CATEGORIES, INCOME_CATEGORIES, format_currency
from extensions import db


MONEY = Decimal('0.01')
MONTHS_PER_YEAR = Decimal('12')

INCOME_CATEGORY_LABELS = dict(INCOME_CATEGORIES)
EXPENSE_CATEGORY_LABELS = dict(EXPENSE_CATEGORIES)
STRATEGY_LABELS = {
    'safe': 'Безопасная',
    'balanced': 'Сбалансированная',
    'aggressive': 'Агрессивное погашение',
}
TARGET_MODE_LABELS = {
    'fixed': 'Фиксированная сумма',
    'one_month': '1 месяц расходов',
    'three_months': '3 месяца расходов',
}
MONTH_LABELS = (
    '', 'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
)

DEFAULT_PREFERENCES = {
    'living_minimum': Decimal('20000.00'),
    'desired_monthly_savings': Decimal('5000.00'),
    'emergency_fund_target_amount': Decimal('30000.00'),
    'emergency_fund_target_mode': 'fixed',
    'strategy': 'balanced',
}

FAMILY_SUPPORT_MARKERS = (
    'родствен', 'помощ', 'маме', 'мама', 'папе', 'папа', 'семье',
    'семья', 'алименты', 'бабуш', 'дедуш',
)


def default_financial_plan_preferences():
    return dict(DEFAULT_PREFERENCES)


def get_financial_plan_preference(user_id):
    if user_id is None:
        return None
    try:
        return FinancialPlanPreference.query.filter_by(user_id=user_id).first()
    except SQLAlchemyError:
        db.session.rollback()
        return None


def preference_values(preference=None):
    if preference is None:
        return default_financial_plan_preferences()
    return {
        'living_minimum': _decimal(preference.living_minimum),
        'desired_monthly_savings': _decimal(preference.desired_monthly_savings),
        'emergency_fund_target_amount': _decimal(preference.emergency_fund_target_amount),
        'emergency_fund_target_mode': preference.emergency_fund_target_mode or 'fixed',
        'strategy': preference.strategy or 'balanced',
    }


def build_financial_plan(user_id, preference=None, today=None, year=None, month=None):
    today = today or date.today()
    period_start = _selected_month_start(today, year, month)
    period_end = period_start + relativedelta(months=1)
    period_last_day = period_end - relativedelta(days=1)
    calculation_date = today if period_start <= today < period_end else period_start
    planning_horizon_end = (
        calculation_date + relativedelta(months=1)
        if period_start <= today < period_end
        else period_last_day
    )
    preferences = preference_values(preference)
    debts, incomes, recurring_expenses = _load_sources(user_id, period_start, period_end)
    emergency_fund = _build_emergency_fund_summary(user_id, period_start, period_end)

    income_summary = _build_income_summary(incomes, period_start)
    debt_items, debt_gaps = _build_debt_items(
        debts,
        calculation_date,
        planning_horizon_end,
    )
    recurring_items = _build_recurring_expense_items(recurring_expenses, period_last_day)
    _mark_debt_expense_duplicates(recurring_items, debt_items)
    counted_recurring_items = [item for item in recurring_items if not item['excluded_as_debt_duplicate']]
    excluded_recurring_items = [item for item in recurring_items if item['excluded_as_debt_duplicate']]

    support_items = [item for item in counted_recurring_items if item['is_family_support']]
    regular_items = [item for item in counted_recurring_items if not item['is_family_support']]

    income_total = _decimal(income_summary['total'])
    debt_total = _decimal(sum((_decimal(item['monthly_amount']) for item in debt_items), Decimal('0.00')))
    support_total = _decimal(sum((_decimal(item['amount']) for item in support_items), Decimal('0.00')))
    regular_total = _decimal(sum((_decimal(item['amount']) for item in regular_items), Decimal('0.00')))
    mandatory_total = _decimal(debt_total + regular_total)
    obligations_total = _decimal(mandatory_total + support_total)

    living_minimum = preferences['living_minimum']
    monthly_needs = _decimal(obligations_total + living_minimum)
    emergency_target = _decimal(_emergency_target(preferences, monthly_needs))
    emergency_current = _decimal(emergency_fund['balance'])
    emergency_gap = _decimal(max(emergency_target - emergency_current, Decimal('0.00')))

    available_after_obligations = _decimal(income_total - obligations_total)
    affordable_for_goals = _decimal(max(available_after_obligations - living_minimum, Decimal('0.00')))
    goals = _build_goals(
        user_id=user_id,
        preferences=preferences,
        emergency_fund=emergency_fund,
        emergency_target=emergency_target,
        period_start=period_start,
        period_end=period_end,
    )
    goal_allocations = _allocate_goal_contributions(goals, affordable_for_goals)
    savings = _decimal(sum((_decimal(item['recommended_contribution']) for item in goals), Decimal('0.00')))
    emergency_savings = _decimal(goals[0]['recommended_contribution'])
    emergency_month_complete = _decimal(goals[0]['monthly_remaining']) <= 0

    extra_repayment_target = _pick_extra_repayment_target(debt_items)
    surplus_after_minimums = _decimal(max(affordable_for_goals - savings, Decimal('0.00')))
    extra_repayment = Decimal('0.00')
    if extra_repayment_target and surplus_after_minimums > 0:
        extra_repayment = _extra_repayment_amount(
            surplus_after_minimums,
            preferences['strategy'],
            emergency_current,
            emergency_target,
            monthly_needs,
        )
        planned_early_amount = _decimal(extra_repayment_target.get('planned_early_repayment_amount'))
        if planned_early_amount > 0:
            extra_repayment = _decimal(min(extra_repayment, planned_early_amount))
        extra_repayment = _decimal(min(
            extra_repayment,
            _decimal(extra_repayment_target.get('projected_remaining_after_plan')),
        ))

    living_budget = _decimal(income_total - obligations_total - savings - extra_repayment)
    weekly_limit = _decimal(max(living_budget, Decimal('0.00')) * MONTHS_PER_YEAR / Decimal('52'))
    daily_limit = _decimal(max(living_budget, Decimal('0.00')) * MONTHS_PER_YEAR / Decimal('365'))
    deficit = _decimal(max(-living_budget, Decimal('0.00')))

    allocation = _build_allocation(
        debt_items=debt_items,
        support_items=support_items,
        regular_items=regular_items,
        goal_allocations=goal_allocations,
        extra_repayment=extra_repayment,
        extra_repayment_target=extra_repayment_target,
        living_budget=max(living_budget, Decimal('0.00')),
        deficit=deficit,
    )
    income_allocations = _build_income_allocations(income_summary['items'], allocation)
    allocated_income_total = _decimal(sum(
        (_decimal(item['allocated_total']) for item in income_allocations),
        Decimal('0.00'),
    ))
    allocation_balance = _decimal(income_total - allocated_income_total)
    state = _financial_state(income_total, obligations_total, living_minimum)
    missing_data = _build_missing_data(income_summary, debt_gaps, recurring_items)
    analysis = _build_analysis(
        state=state,
        income_total=income_total,
        obligations_total=obligations_total,
        savings=emergency_savings,
        living_budget=living_budget,
        extra_repayment=extra_repayment,
        emergency_gap=emergency_gap,
        emergency_month_complete=emergency_month_complete,
    )
    recommendations = _build_recommendations(
        debt_items=debt_items,
        obligations_total=obligations_total,
        savings=emergency_savings,
        extra_repayment=extra_repayment,
        extra_repayment_target=extra_repayment_target,
        emergency_gap=emergency_gap,
        living_budget=living_budget,
        living_minimum=living_minimum,
        emergency_month_complete=emergency_month_complete,
    )
    forecast = _build_forecast(
        debt_items=debt_items,
        savings=emergency_savings,
        emergency_gap=emergency_gap,
        strategy=preferences['strategy'],
        today=calculation_date,
    )

    return {
        'is_demo': user_id is None,
        'today': today,
        'period': {
            'year': period_start.year,
            'month': period_start.month,
            'start': period_start,
            'end': period_end,
            'label': f'{MONTH_LABELS[period_start.month]} {period_start.year}',
            'is_current': period_start.year == today.year and period_start.month == today.month,
            'previous': period_start - relativedelta(months=1),
            'next': period_start + relativedelta(months=1),
            'default_transaction_date': (
                today if period_start <= today < period_end
                else period_last_day if period_start < today
                else period_start
            ),
        },
        'preferences': {
            **{key: _float(value) if isinstance(value, Decimal) else value for key, value in preferences.items()},
            'strategy_label': STRATEGY_LABELS[preferences['strategy']],
            'target_mode_label': TARGET_MODE_LABELS[preferences['emergency_fund_target_mode']],
        },
        'income': income_summary,
        'debt_items': debt_items,
        'regular_items': regular_items,
        'support_items': support_items,
        'excluded_recurring_items': excluded_recurring_items,
        'emergency_fund': emergency_fund,
        'goals': goals,
        'allocation': allocation,
        'income_allocations': income_allocations,
        'analysis': analysis,
        'recommendations': recommendations,
        'forecast': forecast,
        'missing_data': missing_data,
        'state': state,
        'totals': {
            'income': _float(income_total),
            'debt_payments': _float(debt_total),
            'regular_expenses': _float(regular_total),
            'family_support': _float(support_total),
            'mandatory': _float(mandatory_total),
            'obligations': _float(obligations_total),
            'recommended_savings': _float(savings),
            'extra_repayment': _float(extra_repayment),
            'living_budget': _float(living_budget),
            'deficit': _float(deficit),
            'weekly_limit': _float(weekly_limit),
            'daily_limit': _float(daily_limit),
            'emergency_current': _float(emergency_current),
            'emergency_target': _float(emergency_target),
            'emergency_gap': _float(emergency_gap),
            'emergency_progress': _percent(min(emergency_current, emergency_target), emergency_target),
            'allocated_income': _float(allocated_income_total),
            'allocation_balance': _float(allocation_balance),
            'allocation_is_balanced': allocation_balance == 0,
        },
    }


def get_emergency_fund_balance(user_id):
    return _decimal(_build_emergency_fund_summary(user_id)['balance'])


def get_financial_goal_balance(goal_id, user_id):
    goal = FinancialGoal.query.filter_by(id=goal_id, user_id=user_id).first()
    if goal is None:
        return None
    return _decimal(_summarize_goal_transactions(goal.id)['balance'])


def _build_emergency_fund_summary(user_id, period_start=None, period_end=None):
    if user_id is None:
        return {
            'balance': 0.0,
            'deposits': 0.0,
            'withdrawals': 0.0,
            'deposits_in_period': 0.0,
            'withdrawals_in_period': 0.0,
            'transactions': [],
        }
    try:
        query = EmergencyFundTransaction.query.filter_by(user_id=user_id)
        if period_end is not None:
            query = query.filter(EmergencyFundTransaction.transaction_date < period_end)
        transactions = (
            query
            .order_by(
                EmergencyFundTransaction.transaction_date.desc(),
                EmergencyFundTransaction.created_at.desc(),
                EmergencyFundTransaction.id.desc(),
            )
            .all()
        )
    except SQLAlchemyError:
        db.session.rollback()
        transactions = []

    deposits = sum(
        (_decimal(item.amount) for item in transactions if item.transaction_type == 'deposit'),
        Decimal('0.00'),
    )
    withdrawals = sum(
        (_decimal(item.amount) for item in transactions if item.transaction_type == 'withdrawal'),
        Decimal('0.00'),
    )
    deposits_in_period = sum(
        (
            _decimal(item.amount) for item in transactions
            if item.transaction_type == 'deposit'
            and (period_start is None or item.transaction_date >= period_start)
        ),
        Decimal('0.00'),
    )
    withdrawals_in_period = sum(
        (
            _decimal(item.amount) for item in transactions
            if item.transaction_type == 'withdrawal'
            and (period_start is None or item.transaction_date >= period_start)
        ),
        Decimal('0.00'),
    )
    return {
        'balance': _float(deposits - withdrawals),
        'deposits': _float(deposits),
        'withdrawals': _float(withdrawals),
        'deposits_in_period': _float(deposits_in_period),
        'withdrawals_in_period': _float(withdrawals_in_period),
        'transactions': [
            {
                'id': item.id,
                'transaction_type': item.transaction_type,
                'type_label': 'Пополнение' if item.transaction_type == 'deposit' else 'Снятие',
                'amount': _float(item.amount),
                'transaction_date': item.transaction_date,
                'comment': item.comment,
            }
            for item in transactions
        ],
    }


def _build_goals(user_id, preferences, emergency_fund, emergency_target, period_start, period_end):
    emergency_balance = _decimal(emergency_fund['balance'])
    emergency_gap = max(emergency_target - emergency_balance, Decimal('0.00'))
    emergency_monthly_progress = max(
        _decimal(emergency_fund['deposits_in_period'])
        - _decimal(emergency_fund['withdrawals_in_period']),
        Decimal('0.00'),
    )
    emergency_monthly_remaining = min(
        max(preferences['desired_monthly_savings'] - emergency_monthly_progress, Decimal('0.00')),
        emergency_gap,
    )
    goals = [{
        'key': 'emergency',
        'id': None,
        'name': 'Финансовая подушка',
        'is_system': True,
        'priority': 1,
        'target_amount': _float(emergency_target),
        'target_mode': preferences['emergency_fund_target_mode'],
        'target_mode_label': TARGET_MODE_LABELS[preferences['emergency_fund_target_mode']],
        'monthly_contribution': _float(preferences['desired_monthly_savings']),
        'target_date': None,
        'note': 'Резерв на непредвиденные расходы. Эта цель всегда остается первой.',
        'balance': _float(emergency_balance),
        'deposits': emergency_fund['deposits'],
        'withdrawals': emergency_fund['withdrawals'],
        'deposited_this_month': emergency_fund['deposits_in_period'],
        'withdrawn_this_month': emergency_fund['withdrawals_in_period'],
        'monthly_progress': _float(emergency_monthly_progress),
        'monthly_remaining': _float(emergency_monthly_remaining),
        'gap': _float(emergency_gap),
        'progress': _percent(min(emergency_balance, emergency_target), emergency_target),
        'transactions': emergency_fund['transactions'],
        'recommended_contribution': 0.0,
        'monthly_shortfall': 0.0,
    }]

    if user_id is None:
        return goals
    try:
        custom_goals = (
            FinancialGoal.query
            .filter_by(user_id=user_id)
            .order_by(FinancialGoal.priority.asc(), FinancialGoal.id.asc())
            .all()
        )
    except SQLAlchemyError:
        db.session.rollback()
        custom_goals = []

    for index, goal in enumerate(custom_goals, start=2):
        summary = _summarize_goal_transactions(goal.id, period_start, period_end)
        balance = _decimal(summary['balance'])
        target = _decimal(goal.target_amount)
        gap = max(target - balance, Decimal('0.00'))
        monthly_progress = max(
            _decimal(summary['deposits_in_period']) - _decimal(summary['withdrawals_in_period']),
            Decimal('0.00'),
        )
        monthly_remaining = min(
            max(_decimal(goal.monthly_contribution) - monthly_progress, Decimal('0.00')),
            gap,
        )
        goals.append({
            'key': f'goal-{goal.id}',
            'id': goal.id,
            'name': goal.name,
            'is_system': False,
            'priority': index,
            'target_amount': _float(target),
            'target_mode': 'fixed',
            'target_mode_label': 'Фиксированная сумма',
            'monthly_contribution': _float(goal.monthly_contribution),
            'target_date': goal.target_date,
            'note': goal.note,
            'balance': _float(balance),
            'deposits': summary['deposits'],
            'withdrawals': summary['withdrawals'],
            'deposited_this_month': summary['deposits_in_period'],
            'withdrawn_this_month': summary['withdrawals_in_period'],
            'monthly_progress': _float(monthly_progress),
            'monthly_remaining': _float(monthly_remaining),
            'gap': _float(gap),
            'progress': _percent(min(balance, target), target),
            'transactions': summary['transactions'],
            'recommended_contribution': 0.0,
            'monthly_shortfall': 0.0,
        })
    return goals


def _allocate_goal_contributions(goals, affordable_amount):
    remaining = _decimal(affordable_amount)
    allocations = []
    for goal in goals:
        planned = _decimal(goal['monthly_remaining'])
        gap = _decimal(goal['gap'])
        contribution = _decimal(min(planned, gap, remaining))
        goal['recommended_contribution'] = _float(contribution)
        goal['monthly_shortfall'] = _float(max(min(planned, gap) - contribution, Decimal('0.00')))
        remaining = _decimal(remaining - contribution)
        if contribution > 0:
            allocations.append(goal)
    return allocations


def _summarize_goal_transactions(goal_id, period_start=None, period_end=None):
    query = FinancialGoalTransaction.query.filter_by(goal_id=goal_id)
    if period_end is not None:
        query = query.filter(FinancialGoalTransaction.transaction_date < period_end)
    transactions = (
        query
        .order_by(
            FinancialGoalTransaction.transaction_date.desc(),
            FinancialGoalTransaction.created_at.desc(),
            FinancialGoalTransaction.id.desc(),
        )
        .all()
    )
    deposits = sum(
        (_decimal(item.amount) for item in transactions if item.transaction_type == 'deposit'),
        Decimal('0.00'),
    )
    withdrawals = sum(
        (_decimal(item.amount) for item in transactions if item.transaction_type == 'withdrawal'),
        Decimal('0.00'),
    )
    deposits_in_period = sum(
        (
            _decimal(item.amount) for item in transactions
            if item.transaction_type == 'deposit'
            and (period_start is None or item.transaction_date >= period_start)
        ),
        Decimal('0.00'),
    )
    withdrawals_in_period = sum(
        (
            _decimal(item.amount) for item in transactions
            if item.transaction_type == 'withdrawal'
            and (period_start is None or item.transaction_date >= period_start)
        ),
        Decimal('0.00'),
    )
    return {
        'balance': _float(deposits - withdrawals),
        'deposits': _float(deposits),
        'withdrawals': _float(withdrawals),
        'deposits_in_period': _float(deposits_in_period),
        'withdrawals_in_period': _float(withdrawals_in_period),
        'transactions': [
            {
                'id': item.id,
                'transaction_type': item.transaction_type,
                'type_label': 'Пополнение' if item.transaction_type == 'deposit' else 'Снятие',
                'amount': _float(item.amount),
                'transaction_date': item.transaction_date,
                'comment': item.comment,
            }
            for item in transactions
        ],
    }


def _load_sources(user_id, period_start, period_end):
    if user_id is None:
        summary = get_finance_summary(None, period_start.year, period_start.month)
        return (
            list(summary['active_debts']),
            list(summary['incomes_this_month']),
            [expense for expense in summary['expenses_this_month'] if getattr(expense, 'is_monthly', False)],
        )

    debts = (
        Debt.query
        .filter(Debt.user_id == user_id, Debt.status == 'active', Debt.remaining_amount > 0)
        .order_by(Debt.next_payment_date.asc(), Debt.id.asc())
        .all()
    )
    incomes = (
        Income.query
        .filter(
            Income.user_id == user_id,
            Income.income_date >= period_start,
            Income.income_date < period_end,
            Income.category != 'goal_withdrawal',
        )
        .order_by(Income.income_date.asc(), Income.id.asc())
        .all()
    )
    recurring_expenses = (
        Expense.query
        .filter(Expense.user_id == user_id, Expense.is_monthly.is_(True))
        .order_by(Expense.expense_date.asc(), Expense.id.asc())
        .all()
    )
    return debts, incomes, recurring_expenses


def _build_income_summary(incomes, period_start=None):
    items = []
    for income in incomes:
        items.append({
            'id': income.id,
            'amount': _float(income.amount),
            'category': income.category,
            'category_label': INCOME_CATEGORY_LABELS.get(income.category, 'Доход'),
            'source': income.source or INCOME_CATEGORY_LABELS.get(income.category, 'Источник не указан'),
            'date': income.income_date,
        })
    latest_date = max((item['date'] for item in items), default=None)
    period_date = period_start or latest_date
    period_label = f'{MONTH_LABELS[period_date.month]} {period_date.year}' if period_date else None
    return {
        'items': items,
        'total': _float(sum((_decimal(item['amount']) for item in items), Decimal('0.00'))),
        'latest_date': latest_date,
        'period_label': period_label,
    }


def _build_debt_items(debts, today, planning_horizon_end):
    items = []
    gaps = []
    for debt in debts:
        minimum_payment = _decimal(debt.minimum_payment)
        monthly_fee = _decimal(getattr(debt, 'monthly_fee_amount', 0))
        schedule = None
        try:
            schedule = build_debt_payment_schedule(debt, today=today)
        except (ValueError, ArithmeticError, SQLAlchemyError):
            pass

        planned_rows = _planned_schedule_rows(schedule, planning_horizon_end)
        scheduled_amount = _decimal(sum(
            (_decimal(row.get('payment')) for row in planned_rows),
            Decimal('0.00'),
        ))
        paid_toward_next_payment = Decimal('0.00')
        applied_payment_credit = Decimal('0.00')
        partially_paid_due_date = None
        outstanding_rows = list(planned_rows)
        if outstanding_rows:
            first_due_date = date.fromisoformat(outstanding_rows[0]['payment_date'])
            payments = list(getattr(debt, 'payments', []) or [])
            paid_toward_next_payment = min(
                paid_toward_payment_cycle(
                    debt,
                    first_due_date,
                    through_date=today,
                    payments=payments,
                ),
                _decimal(outstanding_rows[0].get('payment')),
            )
            principal_paid_in_cycle = principal_paid_in_payment_cycle(
                debt,
                first_due_date,
                through_date=today,
                payments=payments,
            )
            cycle_opening_balance = _decimal(debt.remaining_amount) + principal_paid_in_cycle
            if minimum_payment <= 0 or cycle_opening_balance > minimum_payment:
                applied_payment_credit = paid_toward_next_payment
            first_outstanding = _decimal(
                _decimal(outstanding_rows[0].get('payment')) - applied_payment_credit
            )
            if first_outstanding > 0:
                partially_paid_due_date = first_due_date
                outstanding_rows[0] = {**outstanding_rows[0], 'payment': _float(first_outstanding)}
            else:
                outstanding_rows = outstanding_rows[1:]

        if schedule is not None:
            monthly_amount = _decimal(sum(
                (_decimal(row.get('payment')) for row in outstanding_rows),
                Decimal('0.00'),
            ))
        else:
            # Conservative fallback: keep the required payment in the plan when
            # an incomplete debt record prevents the detailed schedule from building.
            monthly_amount = _decimal(minimum_payment + monthly_fee)
            scheduled_amount = monthly_amount

        projected_remaining_after_plan = _decimal(debt.remaining_amount)
        if schedule is not None and planned_rows:
            projected_remaining_after_plan = _decimal(
                _decimal(planned_rows[-1].get('remaining')) + applied_payment_credit
            )
            projected_remaining_after_plan = min(
                projected_remaining_after_plan,
                _decimal(debt.remaining_amount),
            )

        scheduled_payments_count = len(outstanding_rows) if schedule is not None else int(monthly_amount > 0)
        interval_label = _debt_interval_label(debt.debt_type, scheduled_payments_count)
        next_payment_date = (
            date.fromisoformat(outstanding_rows[0]['payment_date'])
            if outstanding_rows
            else debt.effective_next_payment_date(today)
            if hasattr(debt, 'effective_next_payment_date')
            else debt.next_payment_date
        )
        planned_payments = [
            {
                'amount': _float(row.get('payment')),
                'due_date': date.fromisoformat(row['payment_date']),
            }
            for row in outstanding_rows
        ]
        if schedule is None and monthly_amount > 0:
            planned_payments = [{
                'amount': _float(monthly_amount),
                'due_date': next_payment_date,
            }]

        end_date, payments_left = _debt_completion(debt, today, schedule=schedule)
        months_left = _months_until(today, end_date) if end_date else None
        effective_rate = debt.interest_rate_for(today) if hasattr(debt, 'interest_rate_for') else debt.interest_rate
        item = {
            'id': debt.id,
            'bank_name': debt.bank_name,
            'product_name': debt.product_name,
            'label': f'{debt.bank_name} — {debt.product_name}',
            'debt_type': debt.debt_type,
            'debt_type_label': Debt.DEBT_TYPE_LABELS.get(debt.debt_type, 'Долг'),
            'remaining': _float(debt.remaining_amount),
            'minimum_payment': _float(minimum_payment),
            'monthly_fee': _float(monthly_fee),
            'monthly_amount': _float(monthly_amount),
            'scheduled_amount': _float(scheduled_amount),
            'paid_toward_next_payment': _float(paid_toward_next_payment),
            'partially_paid_due_date': partially_paid_due_date,
            'scheduled_payments_count': scheduled_payments_count,
            'planned_payments': planned_payments,
            'projected_remaining_after_plan': _float(projected_remaining_after_plan),
            'interest_rate': _float(effective_rate) if effective_rate is not None else None,
            'early_repayment_enabled': bool(getattr(debt, 'early_repayment_enabled', False)),
            'planned_total_payment_amount': _float(getattr(debt, 'planned_early_repayment_amount', 0)),
            'planned_early_repayment_amount': _float(
                debt.effective_planned_early_repayment_amount()
                if hasattr(debt, 'effective_planned_early_repayment_amount')
                else getattr(debt, 'planned_early_repayment_amount', 0)
            ),
            'next_payment_date': next_payment_date,
            'interval_label': interval_label,
            'end_date': end_date,
            'months_left': months_left,
            'payments_left': payments_left,
        }
        items.append(item)

        if minimum_payment <= 0 and debt.debt_type != 'split':
            gaps.append({
                'kind': 'payment',
                'debt_id': debt.id,
                'title': f'Для «{item["label"]}» не указан обязательный платеж.',
                'detail': 'Долг найден, но его сумма не включена в ежемесячное распределение.',
            })
        if effective_rate is None and debt.debt_type != 'split':
            gaps.append({
                'kind': 'rate',
                'debt_id': debt.id,
                'title': f'Для «{item["label"]}» не указана процентная ставка.',
                'detail': 'План платежей рассчитан, но приоритет досрочного погашения менее точен.',
            })
        if not item['next_payment_date']:
            gaps.append({
                'kind': 'date',
                'debt_id': debt.id,
                'title': f'Для «{item["label"]}» не указана дата следующего платежа.',
                'detail': 'Ежемесячная сумма учтена, но дата освобождения денег недоступна.',
            })
        elif minimum_payment > 0 and end_date is None:
            gaps.append({
                'kind': 'schedule',
                'debt_id': debt.id,
                'title': f'Для «{item["label"]}» не удалось построить срок погашения.',
                'detail': 'Платеж включен в бюджет, но параметры долга нужно проверить для прогноза.',
            })
    return items, gaps


def _planned_schedule_rows(schedule, planning_horizon_end):
    if not schedule:
        return []
    return [
        row for row in schedule.get('rows', [])
        if (
            row.get('status') != 'paid'
            and date.fromisoformat(row['payment_date']) <= planning_horizon_end
        )
    ]


def _debt_interval_label(debt_type, payment_count):
    if payment_count <= 0:
        return 'ближайший платёж за пределами расчётного периода'
    if debt_type == 'split':
        if payment_count == 1:
            return 'платёж по графику рассрочки'
        return f'{payment_count} {_payment_noun(payment_count)} по графику рассрочки'
    if payment_count == 1:
        return 'обязательный платёж по графику'
    noun = _payment_noun(payment_count)
    adjective = 'обязательный' if noun == 'платёж' else 'обязательных'
    return f'{payment_count} {adjective} {noun} по графику'


def _payment_noun(value):
    value = abs(int(value))
    if value % 100 in range(11, 15):
        return 'платежей'
    if value % 10 == 1:
        return 'платёж'
    if value % 10 in (2, 3, 4):
        return 'платежа'
    return 'платежей'


def _build_recurring_expense_items(expenses, today):
    groups = defaultdict(list)
    for expense in expenses:
        key = expense.monthly_group_id or f'expense-{expense.id}'
        groups[key].append(expense)

    items = []
    for group in groups.values():
        current = _current_recurring_expense(group, today)
        due_date = date(
            today.year,
            today.month,
            min(current.expense_date.day, today.day),
        )
        searchable_text = ' '.join(filter(None, (current.title, current.comment))).lower()
        items.append({
            'id': current.id,
            'amount': _float(current.amount),
            'title': current.title,
            'category': current.category,
            'category_label': EXPENSE_CATEGORY_LABELS.get(current.category, 'Другое'),
            'expense_date': current.expense_date,
            'due_date': due_date,
            'is_family_support': any(marker in searchable_text for marker in FAMILY_SUPPORT_MARKERS),
            'excluded_as_debt_duplicate': False,
            'duplicate_debt_label': None,
        })
    return sorted(items, key=lambda item: (item['due_date'], -item['amount'], item['title'].lower()))


def _current_recurring_expense(group, today):
    not_future = [expense for expense in group if expense.expense_date <= today]
    candidates = not_future or group
    return max(candidates, key=lambda expense: (expense.expense_date, expense.id or 0))


def _mark_debt_expense_duplicates(expense_items, debt_items):
    for expense in expense_items:
        if expense['category'] != 'loans':
            continue
        expense_title = _search_key(expense['title'])
        for debt in debt_items:
            debt_names = (_search_key(debt['bank_name']), _search_key(debt['product_name']))
            amount_matches = abs(_decimal(expense['amount']) - _decimal(debt['monthly_amount'])) <= MONEY
            title_matches = any(name and name in expense_title for name in debt_names)
            if amount_matches or title_matches:
                expense['excluded_as_debt_duplicate'] = True
                expense['duplicate_debt_label'] = debt['label']
                break


def _debt_completion(debt, today, schedule=None):
    if (
        not debt.next_payment_date
        or (_decimal(debt.minimum_payment) <= 0 and debt.debt_type != 'split')
    ):
        return None, None
    if schedule is None:
        try:
            schedule = build_debt_payment_schedule(debt, today=today)
        except (ValueError, ArithmeticError, SQLAlchemyError):
            return None, None
    future_rows = [
        row for row in schedule.get('rows', [])
        if date.fromisoformat(row['payment_date']) >= today and row.get('status') != 'paid'
    ]
    if not future_rows:
        return None, 0
    return date.fromisoformat(future_rows[-1]['payment_date']), len(future_rows)


def _emergency_target(preferences, monthly_needs):
    mode = preferences['emergency_fund_target_mode']
    if mode == 'one_month':
        return monthly_needs
    if mode == 'three_months':
        return monthly_needs * Decimal('3')
    return preferences['emergency_fund_target_amount']


def _pick_extra_repayment_target(debt_items):
    if not debt_items:
        return None
    configured_targets = [
        item for item in debt_items
        if (
            item['debt_type'] == 'consumer_credit'
            and item['early_repayment_enabled']
            and _decimal(item['planned_early_repayment_amount']) > 0
        )
    ]
    candidates = configured_targets or debt_items
    return max(
        candidates,
        key=lambda item: (
            item['interest_rate'] is not None,
            item['interest_rate'] or 0,
            item['remaining'],
        ),
    )


def _extra_repayment_amount(surplus, strategy, current_fund, target_fund, monthly_needs):
    reserve_floor = min(monthly_needs, target_fund)
    if current_fund < reserve_floor:
        return Decimal('0.00')
    if current_fund < target_fund:
        ratio = {'safe': Decimal('0'), 'balanced': Decimal('0.25'), 'aggressive': Decimal('0.50')}[strategy]
    else:
        ratio = {'safe': Decimal('0.25'), 'balanced': Decimal('0.60'), 'aggressive': Decimal('1')}[strategy]
    return _decimal(surplus * ratio)


def _build_allocation(
    debt_items,
    support_items,
    regular_items,
    goal_allocations,
    extra_repayment,
    extra_repayment_target,
    living_budget,
    deficit,
):
    obligations = []
    for debt in debt_items:
        if debt['monthly_amount'] <= 0:
            continue
        planned_payments = debt.get('planned_payments') or [{
            'amount': debt['monthly_amount'],
            'due_date': debt['next_payment_date'],
        }]
        payment_count = len(planned_payments)
        for index, payment in enumerate(planned_payments, start=1):
            base_detail = debt['interval_label']
            if payment_count > 1:
                base_detail = f'платёж {index} из {payment_count} по графику'
            paid_toward_next_payment = _decimal(debt.get('paid_toward_next_payment'))
            if payment['due_date'] == debt.get('partially_paid_due_date') and paid_toward_next_payment > 0:
                base_detail = (
                    f'{base_detail} · уже внесено '
                    f'{format_currency(paid_toward_next_payment)}'
                )
            obligations.append({
                'kind': 'debt',
                'icon': 'bi-bank',
                'label': debt['label'],
                'detail': _detail_with_due_date(base_detail, payment['due_date']),
                'base_detail': base_detail,
                'amount': payment['amount'],
                'source_id': debt['id'],
                'due_date': payment['due_date'],
            })
    for expense in support_items:
        obligations.append({
            'kind': 'support',
            'icon': 'bi-people',
            'label': expense['title'],
            'detail': _detail_with_due_date('регулярная помощь', expense['due_date']),
            'base_detail': 'регулярная помощь',
            'amount': expense['amount'],
            'source_id': expense['id'],
            'due_date': expense['due_date'],
        })
    for expense in regular_items:
        obligations.append({
            'kind': 'expense',
            'icon': 'bi-repeat',
            'label': expense['title'],
            'detail': _detail_with_due_date(expense['category_label'], expense['due_date']),
            'base_detail': expense['category_label'],
            'amount': expense['amount'],
            'source_id': expense['id'],
            'due_date': expense['due_date'],
        })
    obligations.sort(key=lambda item: (
        item['due_date'] is None,
        item['due_date'] or date.max,
        item['label'].lower(),
    ))
    items = list(obligations)
    for goal in goal_allocations:
        monthly_plan = _decimal(goal['monthly_contribution'])
        monthly_progress = _decimal(goal['monthly_progress'])
        monthly_remaining = _decimal(goal['monthly_remaining'])
        recommended = _decimal(goal['recommended_contribution'])
        detail_parts = [f'план месяца {format_currency(monthly_plan)}']
        if monthly_progress > 0:
            detail_parts.append(f'выполнено {format_currency(monthly_progress)}')
        if recommended < monthly_remaining:
            detail_parts.append(
                f'сейчас доступно {format_currency(recommended)} из {format_currency(monthly_remaining)}'
            )
        detail_parts.append(f'приоритет №{goal["priority"]}')
        detail = ' · '.join(detail_parts)
        items.append({
            'kind': 'savings',
            'icon': 'bi-shield-check' if goal['is_system'] else 'bi-bullseye',
            'label': goal['name'],
            'detail': detail,
            'base_detail': detail,
            'amount': goal['recommended_contribution'],
            'goal_priority': goal['priority'],
            'goal_monthly_plan': goal['monthly_contribution'],
            'goal_monthly_progress': goal['monthly_progress'],
            'goal_monthly_remaining': goal['monthly_remaining'],
            'goal_recommended': goal['recommended_contribution'],
        })
    if extra_repayment > 0 and extra_repayment_target:
        items.append({
            'kind': 'extra',
            'icon': 'bi-lightning-charge',
            'label': f'Досрочно: {extra_repayment_target["label"]}',
            'detail': 'сверх обязательного платежа',
            'amount': _float(extra_repayment),
            'source_id': extra_repayment_target['id'],
        })
    if living_budget > 0:
        items.append({
            'kind': 'living',
            'icon': 'bi-bag-check',
            'label': 'Повседневные расходы',
            'detail': 'доступно после распределения',
            'amount': _float(living_budget),
        })
    if deficit > 0:
        items.append({
            'kind': 'deficit',
            'icon': 'bi-exclamation-triangle',
            'label': 'Непокрытый дефицит',
            'detail': 'обязательства превышают доступный доход',
            'amount': _float(deficit),
        })
    return items


def _detail_with_due_date(detail, due_date):
    if due_date is None:
        return detail
    return f'{detail} · срок {due_date.strftime("%d.%m.%Y")}'


def _income_due_context(due_date, income_date):
    if due_date is None or income_date is None:
        return None, None
    if due_date < income_date:
        return f'срок уже наступил: {due_date.strftime("%d.%m.%Y")}', 'overdue'
    if due_date == income_date:
        return 'оплатить сегодня', 'today'
    days_until_due = (due_date - income_date).days
    if days_until_due <= 7:
        return f'оплатить до {due_date.strftime("%d.%m.%Y")} · через {days_until_due} дн.', 'upcoming'
    return f'оплатить до {due_date.strftime("%d.%m.%Y")}', 'planned'


def _build_income_allocations(income_items, monthly_allocation):
    """Split the monthly recommendation between actual income entries in date order."""
    destinations = []
    for item in monthly_allocation:
        if item['kind'] == 'deficit':
            continue
        amount = _decimal(item['amount'])
        if amount <= 0:
            continue
        destinations.append({
            'item': item,
            'initial': amount,
            'remaining': amount,
        })

    allocations = []
    destination_index = 0
    ordered_incomes = sorted(
        income_items,
        key=lambda item: (item['date'] or date.min, item['id'] or 0),
    )
    for income in ordered_incomes:
        available = _decimal(income['amount'])
        recommended = []

        while available > 0 and destination_index < len(destinations):
            destination = destinations[destination_index]
            if destination['remaining'] <= 0:
                destination_index += 1
                continue

            directed_amount = _decimal(min(available, destination['remaining']))
            source_item = destination['item']
            allocated_before = _decimal(destination['initial'] - destination['remaining'])
            due_label, urgency = _income_due_context(
                source_item.get('due_date'),
                income['date'],
            )
            detail = source_item.get('base_detail', source_item['detail'])
            if source_item['kind'] == 'savings' and allocated_before > 0:
                detail = (
                    f'{detail} · ранее распределено '
                    f'{format_currency(allocated_before)} из предыдущих поступлений'
                )
            if due_label:
                detail = f'{due_label} · {detail}'
            recommended.append({
                'kind': source_item['kind'],
                'icon': source_item['icon'],
                'label': source_item['label'],
                'detail': detail,
                'amount': _float(directed_amount),
                'due_date': source_item.get('due_date'),
                'urgency': urgency,
            })
            available = _decimal(available - directed_amount)
            destination['remaining'] = _decimal(destination['remaining'] - directed_amount)

        if available > 0:
            recommended.append({
                'kind': 'living',
                'icon': 'bi-wallet2',
                'label': 'Свободный остаток',
                'detail': 'после плановых направлений месяца',
                'amount': _float(available),
            })
            available = Decimal('0.00')

        allocations.append({
            **income,
            'allocations': recommended,
            'allocated_total': _float(sum(
                (_decimal(item['amount']) for item in recommended),
                Decimal('0.00'),
            )),
        })

    return list(reversed(allocations))


def _financial_state(income, obligations, living_minimum):
    remaining = income - obligations
    if income <= 0 or remaining < 0:
        return {'key': 'deficit', 'label': 'Требует внимания', 'tone': 'danger'}
    if remaining < living_minimum or obligations / income >= Decimal('0.70'):
        return {'key': 'strained', 'label': 'Бюджет напряженный', 'tone': 'warning'}
    return {'key': 'positive', 'label': 'Бюджет устойчивый', 'tone': 'success'}


def _build_analysis(
    state,
    income_total,
    obligations_total,
    savings,
    living_budget,
    extra_repayment,
    emergency_gap,
    emergency_month_complete=False,
):
    if state['key'] == 'deficit':
        lead = (
            f'Текущего дохода {format_currency(income_total)} недостаточно для всех обязательств '
            f'на сумму {format_currency(obligations_total)}.'
        )
    elif state['key'] == 'strained':
        lead = (
            f'Бюджет остается положительным, но после обязательств остается '
            f'{format_currency(income_total - obligations_total)}.'
        )
    else:
        lead = (
            f'При текущем доходе {format_currency(income_total)} все обязательства выполняются '
            f'без дефицита.'
        )

    details = []
    if living_budget >= 0:
        details.append(
            f'После распределения на повседневные расходы остается {format_currency(living_budget)}.'
        )
    else:
        details.append(
            f'Для полного плана не хватает {format_currency(abs(living_budget))}; накопления временно не рекомендуются.'
        )
    if savings > 0:
        details.append(f'В финансовую подушку можно направить {format_currency(savings)}.')
    elif emergency_gap > 0 and emergency_month_complete:
        details.append('План пополнения финансовой подушки на этот месяц уже выполнен.')
    elif emergency_gap > 0 and living_budget >= 0:
        details.append('Накопления не заложены: свободная сумма не превышает выбранный минимум на жизнь.')
    if extra_repayment > 0:
        details.append(f'На досрочное погашение доступно {format_currency(extra_repayment)}.')
    elif emergency_gap > 0:
        details.append('Досрочное погашение пока отложено до создания минимального резерва.')
    return {'title': state['label'], 'lead': lead, 'details': details}


def _build_recommendations(
    debt_items,
    obligations_total,
    savings,
    extra_repayment,
    extra_repayment_target,
    emergency_gap,
    living_budget,
    living_minimum,
    emergency_month_complete=False,
):
    items = [{
        'stage': 'Сейчас',
        'title': 'Сначала зарезервировать обязательные платежи',
        'amount': _float(obligations_total),
        'reason': 'Эти суммы уже найдены в действующих долгах и регулярных расходах.',
        'tone': 'primary',
    }]
    if emergency_gap > 0:
        items.append({
            'stage': 'Приоритет №1',
            'title': (
                'План пополнения подушки на месяц выполнен'
                if emergency_month_complete
                else 'Продолжить формирование финансовой подушки'
            ),
            'amount': None if emergency_month_complete else _float(savings),
            'reason': (
                'Следующее плановое пополнение будет рассчитано для нового месяца.'
                if emergency_month_complete
                else (
                    f'До выбранной цели не хватает {format_currency(emergency_gap)}. '
                    'Резерв снижает риск нового долга при непредвиденных расходах.'
                )
            ),
            'tone': 'success',
        })

    small_debts = [
        debt for debt in debt_items
        if debt['remaining'] <= max(debt['monthly_amount'] * 6, 50000) and debt['remaining'] > 0
    ]
    if small_debts:
        target = min(small_debts, key=lambda debt: debt['remaining'])
        items.append({
            'stage': 'Следующий шаг',
            'title': f'Закрыть небольшой долг: {target["label"]}',
            'amount': target['remaining'],
            'reason': (
                f'После закрытия освободится около {format_currency(target["monthly_amount"])} в месяц '
                'для накоплений или основного кредита.'
            ),
            'tone': 'warning',
        })
    if extra_repayment_target:
        items.append({
            'stage': 'Досрочное погашение',
            'title': f'{format_currency(extra_repayment)} → {extra_repayment_target["label"]}',
            'amount': None,
            'reason': (
                'Сейчас дополнительный платеж равен нулю, потому что финансовая подушка ниже безопасного уровня.'
                if extra_repayment <= 0 and emergency_gap > 0
                else 'Цель выбрана по максимальной известной ставке среди активных долгов.'
            ),
            'tone': 'neutral',
        })
    if living_budget < living_minimum:
        items.append({
            'stage': 'Контроль риска',
            'title': 'Не увеличивать накопления за счет повседневного бюджета',
            'amount': None,
            'reason': (
                f'Доступно {format_currency(max(living_budget, 0))}, что ниже выбранного минимума '
                f'{format_currency(living_minimum)}.'
            ),
            'tone': 'danger',
        })
    return items


def _build_forecast(debt_items, savings, emergency_gap, strategy, today):
    events = []
    release_ratios = {
        'safe': (Decimal('0.70'), Decimal('0.30')),
        'balanced': (Decimal('0.45'), Decimal('0.55')),
        'aggressive': (Decimal('0.25'), Decimal('0.75')),
    }
    savings_ratio, debt_ratio = release_ratios[strategy]
    for debt in sorted(
        (item for item in debt_items if item['end_date']),
        key=lambda item: item['end_date'],
    )[:4]:
        released = _decimal(debt['monthly_amount'])
        events.append({
            'kind': 'debt_closed',
            'date': debt['end_date'],
            'months_from_now': debt['months_left'],
            'title': f'Закроется {debt["label"]}',
            'released': _float(released),
            'savings_amount': _float(released * savings_ratio),
            'debt_amount': _float(released * debt_ratio),
            'description': (
                'Высвободившийся платеж можно разделить между подушкой и досрочным погашением '
                'в пропорции выбранной стратегии.'
            ),
        })

    cushion_event = None
    if emergency_gap > 0 and savings > 0:
        months = int((emergency_gap / savings).to_integral_value(rounding=ROUND_CEILING))
        target_date = today + relativedelta(months=months)
        cushion_event = {
            'kind': 'emergency_ready',
            'date': target_date,
            'months_from_now': months,
            'title': 'Финансовая подушка достигнет цели',
            'released': _float(savings),
            'description': (
                f'После этого ежемесячные {format_currency(savings)} можно перенаправить '
                'на следующую цель или досрочное погашение.'
            ),
        }
    return {'events': events, 'cushion_event': cushion_event}


def _build_missing_data(income_summary, debt_gaps, recurring_items):
    items = list(debt_gaps)
    if not income_summary['items']:
        items.insert(0, {
            'kind': 'income',
            'title': 'Не указан ежемесячный доход.',
            'detail': 'Без дохода распределение показывает только найденные обязательства.',
        })
    if not recurring_items:
        items.append({
            'kind': 'expenses',
            'title': 'Регулярные расходы не найдены.',
            'detail': 'Коммунальные услуги, подписки и помощь можно пометить как ежемесячные в расходах.',
        })
    return items


def _months_until(start, end):
    if not end:
        return None
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day > start.day:
        months += 1
    return max(months, 1)


def _selected_month_start(today, year=None, month=None):
    if year is None or month is None:
        return date(today.year, today.month, 1)
    try:
        return date(int(year), int(month), 1)
    except (TypeError, ValueError):
        return date(today.year, today.month, 1)


def _percent(part, total):
    if total <= 0:
        return 0
    return round(float(part / total * Decimal('100')), 1)


def _decimal(value):
    if value is None:
        return Decimal('0.00')
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _float(value):
    return float(_decimal(value))


def _search_key(value):
    return ''.join(character for character in str(value or '').lower() if character.isalnum())
