"""Financial goals and the emergency fund as transfers of the user's own money.

Deposits to and withdrawals from goals are not incomes or expenses: they only
move money between "free" and "set aside". Month totals therefore subtract
the net transfer (deposits − withdrawals) separately from expenses.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_CEILING, Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy import func

from app.models import EmergencyFundTransaction, FinancialGoal, FinancialGoalTransaction
from extensions import db


EMERGENCY_KEY = 'emergency'
TRANSACTION_TYPES = ('deposit', 'withdrawal')
TYPE_LABELS = {'deposit': 'Пополнение', 'withdrawal': 'Снятие'}
EMERGENCY_NAME = 'Финансовая подушка'
PACE_MONTHS = 3
ZERO = Decimal('0.00')
MONTH_LABELS = (
    '', 'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
)


class GoalNotFound(LookupError):
    """The goal or operation does not exist or belongs to another user."""


@dataclass(frozen=True)
class GoalRef:
    key: str
    user_id: int
    goal: FinancialGoal | None = None

    @property
    def is_emergency(self):
        return self.goal is None

    @property
    def name(self):
        return EMERGENCY_NAME if self.goal is None else self.goal.name

    @property
    def entity_type(self):
        return 'emergency_fund_transaction' if self.goal is None else 'goal_transaction'

    def query(self):
        if self.goal is None:
            return EmergencyFundTransaction.query.filter_by(user_id=self.user_id)
        return FinancialGoalTransaction.query.filter_by(goal_id=self.goal.id)

    def new_transaction(self, **values):
        if self.goal is None:
            return EmergencyFundTransaction(user_id=self.user_id, **values)
        return FinancialGoalTransaction(goal_id=self.goal.id, **values)


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.01'))


def goal_key(goal_id):
    return EMERGENCY_KEY if goal_id is None else str(goal_id)


def get_goal_ref(user_id, key):
    """Resolve a goal key ('emergency' or a goal id) for its owner only."""
    if user_id is None:
        raise GoalNotFound(key)
    if str(key) == EMERGENCY_KEY:
        return GoalRef(EMERGENCY_KEY, user_id)
    try:
        goal_id = int(key)
    except (TypeError, ValueError):
        raise GoalNotFound(key) from None
    goal = FinancialGoal.query.filter_by(id=goal_id, user_id=user_id).first()
    if goal is None:
        raise GoalNotFound(key)
    return GoalRef(str(goal.id), user_id, goal)


def _chronological(transactions):
    # Deposits of a day come before its withdrawals, so taking out money put in
    # earlier the same day is never reported as a negative balance.
    return sorted(
        transactions,
        key=lambda item: (
            item.transaction_date,
            0 if item.transaction_type == 'deposit' else 1,
            item.created_at or datetime.max,
            item.id or 0,
        ),
    )


def _first_negative_balance(transactions):
    balance = ZERO
    for item in _chronological(transactions):
        amount = _money(item.amount)
        balance += amount if item.transaction_type == 'deposit' else -amount
        if balance < 0:
            return item
    return None


def _validate_values(transaction_type, amount, transaction_date, comment):
    if transaction_type not in TRANSACTION_TYPES:
        raise ValueError('Выберите корректный тип операции.')
    if amount is None or amount <= 0:
        raise ValueError('Сумма операции должна быть больше нуля.')
    if transaction_date is None:
        raise ValueError('Укажите дату операции.')
    if comment and len(comment) > 255:
        raise ValueError('Комментарий не должен превышать 255 символов.')


def _ensure_history_stays_positive(ref, transactions):
    if _first_negative_balance(transactions) is not None:
        if ref.is_emergency:
            raise ValueError('Нельзя снять больше, чем накоплено в подушке на эту дату.')
        raise ValueError(f'Нельзя снять больше, чем накоплено на цель «{ref.name}» на эту дату.')


def add_operation(ref, transaction_type, amount, transaction_date, comment=None):
    """Record a transfer to or from a goal. No Income or Expense is created."""
    comment = (comment or '').strip() or None
    _validate_values(transaction_type, amount, transaction_date, comment)
    transaction = ref.new_transaction(
        transaction_type=transaction_type,
        amount=_money(amount),
        transaction_date=transaction_date,
        comment=comment,
    )
    _ensure_history_stays_positive(ref, [*ref.query().all(), transaction])
    db.session.add(transaction)
    db.session.commit()
    return transaction


def get_operation(ref, transaction_id):
    transaction = ref.query().filter_by(id=transaction_id).first()
    if transaction is None:
        raise GoalNotFound(transaction_id)
    return transaction


def update_operation(ref, transaction_id, transaction_type, amount, transaction_date, comment=None):
    transaction = get_operation(ref, transaction_id)
    comment = (comment or '').strip() or None
    _validate_values(transaction_type, amount, transaction_date, comment)
    with db.session.no_autoflush:
        transaction.transaction_type = transaction_type
        transaction.amount = _money(amount)
        transaction.transaction_date = transaction_date
        transaction.comment = comment
        try:
            _ensure_history_stays_positive(ref, ref.query().all())
        except ValueError:
            db.session.rollback()
            raise
    db.session.commit()
    return transaction


def delete_operation(ref, transaction_id):
    transaction = get_operation(ref, transaction_id)
    remaining = [item for item in ref.query().all() if item.id != transaction.id]
    if _first_negative_balance(remaining) is not None:
        raise ValueError('Сначала удалите связанные снятия: без этого остаток станет отрицательным.')
    db.session.delete(transaction)
    db.session.commit()


def operation_history(ref):
    """All operations newest first, each with the balance right after it."""
    running = ZERO
    rows = []
    for item in _chronological(ref.query().all()):
        amount = _money(item.amount)
        running += amount if item.transaction_type == 'deposit' else -amount
        rows.append({
            'id': item.id,
            'transaction_type': item.transaction_type,
            'type_label': TYPE_LABELS[item.transaction_type],
            'amount': amount,
            'transaction_date': item.transaction_date,
            'comment': item.comment,
            'balance_after': running,
        })
    rows.reverse()
    return rows


def monthly_summary(history):
    """Per-month deposits, withdrawals, net and the balance at month end."""
    months = {}
    for row in sorted(history, key=lambda item: item['transaction_date']):
        key = row['transaction_date'].replace(day=1)
        month = months.setdefault(key, {
            'month_start': key,
            'label': f'{MONTH_LABELS[key.month]} {key.year}',
            'deposited': ZERO,
            'withdrawn': ZERO,
        })
        if row['transaction_type'] == 'deposit':
            month['deposited'] += row['amount']
        else:
            month['withdrawn'] += row['amount']
    running = ZERO
    result = []
    for key in sorted(months):
        month = months[key]
        month['net'] = month['deposited'] - month['withdrawn']
        running += month['net']
        month['balance_end'] = running
        result.append(month)
    result.reverse()
    return result


def _months_needed(gap, per_month):
    return int((gap / per_month).to_integral_value(rounding=ROUND_CEILING))


def forecast(current_balance, target_amount, history, monthly_contribution, today=None):
    """When the goal is reached at the recent pace and at the planned contribution.

    The pace is the average net transfer over the last PACE_MONTHS full months.
    """
    today = today or date.today()
    target = _money(target_amount)
    current_balance = _money(current_balance)
    gap = max(target - current_balance, ZERO)
    current_month = today.replace(day=1)
    window_start = current_month - relativedelta(months=PACE_MONTHS)
    window_net = sum(
        (
            row['amount'] if row['transaction_type'] == 'deposit' else -row['amount']
            for row in history
            if window_start <= row['transaction_date'] < current_month
        ),
        ZERO,
    )
    pace = _money(window_net / PACE_MONTHS)
    planned = _money(monthly_contribution)
    result = {
        'gap': gap,
        'is_reached': target > 0 and gap == 0,
        'pace': pace,
        'pace_months': PACE_MONTHS,
        'pace_date': None,
        'pace_months_left': None,
        'planned': planned,
        'planned_date': None,
        'planned_months_left': None,
    }
    if result['is_reached']:
        return result
    if pace > 0:
        months = _months_needed(gap, pace)
        result['pace_months_left'] = months
        result['pace_date'] = current_month + relativedelta(months=months)
    if planned > 0:
        months = _months_needed(gap, planned)
        result['planned_months_left'] = months
        result['planned_date'] = current_month + relativedelta(months=months)
    return result


def empty_goal_transfers():
    return {'deposits': ZERO, 'withdrawals': ZERO, 'net': ZERO}


def month_transfers(user_id, month_start, month_end):
    """Deposits, withdrawals and their net for all goals and the fund in a month."""
    if user_id is None:
        return empty_goal_transfers()
    totals = {'deposit': ZERO, 'withdrawal': ZERO}
    goal_rows = (
        db.session.query(FinancialGoalTransaction.transaction_type, func.coalesce(func.sum(FinancialGoalTransaction.amount), 0))
        .join(FinancialGoal, FinancialGoal.id == FinancialGoalTransaction.goal_id)
        .filter(
            FinancialGoal.user_id == user_id,
            FinancialGoalTransaction.transaction_date >= month_start,
            FinancialGoalTransaction.transaction_date < month_end,
        )
        .group_by(FinancialGoalTransaction.transaction_type)
        .all()
    )
    fund_rows = (
        db.session.query(EmergencyFundTransaction.transaction_type, func.coalesce(func.sum(EmergencyFundTransaction.amount), 0))
        .filter(
            EmergencyFundTransaction.user_id == user_id,
            EmergencyFundTransaction.transaction_date >= month_start,
            EmergencyFundTransaction.transaction_date < month_end,
        )
        .group_by(EmergencyFundTransaction.transaction_type)
        .all()
    )
    for transaction_type, amount in [*goal_rows, *fund_rows]:
        totals[transaction_type] += _money(amount)
    return {
        'deposits': totals['deposit'],
        'withdrawals': totals['withdrawal'],
        'net': totals['deposit'] - totals['withdrawal'],
    }


def goal_overview(user_id, plan_goals, today=None):
    """Cards for the goals page from the plan's goal settings and the full history."""
    today = today or date.today()
    cards = []
    for plan_goal in plan_goals:
        ref = get_goal_ref(user_id, goal_key(plan_goal['id']))
        history = operation_history(ref)
        current = history[0]['balance_after'] if history else ZERO
        target = _money(plan_goal['target_amount'])
        cards.append({
            'key': ref.key,
            'name': ref.name,
            'is_emergency': ref.is_emergency,
            'balance': current,
            'target_amount': target,
            'progress': float(min(current / target * 100, Decimal('100'))) if target > 0 else 0.0,
            'monthly_contribution': _money(plan_goal['monthly_contribution']),
            'target_date': plan_goal.get('target_date'),
            'operations_count': len(history),
            'forecast': forecast(current, target, history, plan_goal['monthly_contribution'], today),
        })
    return cards
