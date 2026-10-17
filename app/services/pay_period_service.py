"""Recommend where each income entry of a month should go.

The only notion is the next income: the next salary or advance (or an income
marked by hand as a main payment). Without a recorded one it is expected a
calendar month after a known payment. In date order, an income:

1. pays the obligations already due and still uncovered (overdue ones and
   those between two incomes);
2. pays the obligations due before the next income, nearest first;
3. keeps the share of the monthly living minimum for the days until the next
   income;
4. pays early the obligations due within a month of its date, nearest first;
5. funds goals and early repayment, and leaves the rest for everyday spending.

Nothing is moved by itself: every line is a recommendation. A missing living
reserve or an uncovered obligation becomes a warning on the income card.
Dates at the start of the month that the previous month's last income was
expected to cover are listed as covered by it.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta

from app.utils import format_currency


MONEY = Decimal('0.01')
ZERO = Decimal('0.00')

# Income history read around the selected month to find the next incomes.
PAY_HISTORY_LOOKBACK_DAYS = 92
PAY_HISTORY_LOOKAHEAD_DAYS = 45
PERIOD_OPENING_CATEGORIES = ('salary', 'advance')

# Manual mark of an income: it always is, or never is, a main payment.
PAY_PERIOD_MODE_START = 'start'
PAY_PERIOD_MODE_JOIN = 'join'
PAY_PERIOD_MODES = (PAY_PERIOD_MODE_START, PAY_PERIOD_MODE_JOIN)
PAY_PERIOD_MODE_LABELS = {
    '': 'Определять по категории',
    PAY_PERIOD_MODE_START: 'Основная выплата (как зарплата или аванс)',
    PAY_PERIOD_MODE_JOIN: 'Разовое поступление',
}

OBLIGATION_KINDS = ('debt', 'support', 'expense')
POOL_KINDS = ('savings', 'extra')


def parse_pay_period_mode(value):
    """Form value of the manual mark; empty means the category decides."""
    value = str(value or '').strip()
    if not value:
        return None
    if value not in PAY_PERIOD_MODES:
        raise ValueError('Выберите корректный вид поступления')
    return value


def is_main_payment(category, mode=None):
    """Salaries and advances, or incomes marked as a main payment, set the next income."""
    if mode == PAY_PERIOD_MODE_START:
        return True
    if mode == PAY_PERIOD_MODE_JOIN:
        return False
    return category in PERIOD_OPENING_CATEGORIES


def main_payment_dates(income_items, history_incomes=()):
    """Dates of main payments; with none at all, every income counts."""
    entries = [
        (item['date'], item['category'], item.get('pay_period_mode'))
        for item in list(income_items) + list(history_incomes or ())
    ]
    dates = {day for day, category, mode in entries if day and is_main_payment(category, mode)}
    if not dates:
        dates = {day for day, _category, mode in entries if day and mode != PAY_PERIOD_MODE_JOIN}
    return dates


def next_income_after(day, payment_dates):
    """The next main payment after ``day``: recorded, or a month after a known one.

    Returns ``(date, is_estimated)``, or ``(None, False)`` without any payment.
    """
    recorded = min((other for other in payment_dates if other > day), default=None)
    expected = min(
        (
            projected for projected in (other + relativedelta(months=1) for other in payment_dates)
            if projected > day
        ),
        default=None,
    )
    if recorded is not None and (expected is None or recorded <= expected):
        return recorded, False
    if expected is not None:
        return expected, True
    return None, False


def recommend_income_allocation(
    income_items,
    monthly_allocation,
    period_start,
    period_end=None,
    history_incomes=None,
    next_month_obligations=(),
    living_minimum=ZERO,
):
    """Recommend directions for every income entry of the month.

    ``history_incomes`` (dicts with ``date``, ``category``, ``pay_period_mode``)
    covers the months around the selected one and only helps to find the next
    incomes. ``next_month_obligations`` are recurring payments of the following
    month: not part of the month's totals, they only take money that reaches
    them. ``living_minimum`` is the monthly everyday-spending minimum.
    """
    period_end = period_end or _month_end(period_start)
    living_minimum = _decimal(living_minimum)
    incomes = sorted(income_items, key=lambda item: (item['date'] or date.min, item['id'] or 0))
    payment_dates = main_payment_dates(incomes, history_incomes)

    obligations = []
    pools = []
    for item in monthly_allocation:
        amount = _decimal(item['amount'])
        if amount <= 0:
            continue
        if item['kind'] in OBLIGATION_KINDS:
            obligations.append(_entry(item, amount))
        elif item['kind'] in POOL_KINDS:
            pools.append(_entry(item, amount))
    for item in next_month_obligations:
        amount = _decimal(item['amount'])
        if amount > 0 and item.get('due_date') is not None:
            obligations.append(_entry(item, amount, next_month=True))
    obligations.sort(key=_obligation_order)

    covered_by_previous = []
    if incomes:
        covered_by_previous = _cover_from_previous_month(
            obligations, payment_dates, period_start, incomes[0]['date'],
        )

    reserved_until = None
    short_rows = []
    rows = []
    for income in incomes:
        day = income['date']
        next_date, next_estimated = next_income_after(day, payment_dates)
        horizon = next_date or period_end
        ahead_until = day + relativedelta(months=1)
        if reserved_until is not None and day < reserved_until:
            # An income inside a span already reserved for first tops up what
            # the earlier incomes could not keep.
            catch_up = _decimal(sum((_decimal(row['reserve_missing']) for row in short_rows), ZERO))
            needed = _decimal(catch_up + _reserve_needed(living_minimum, reserved_until, horizon))
        else:
            short_rows = []
            catch_up = ZERO
            needed = _reserve_needed(living_minimum, day, horizon)
        row = _allocate(income, obligations, pools, day, horizon, ahead_until, needed)
        if catch_up > 0:
            # The later income now carries whatever is still missing in the span.
            for earlier in short_rows:
                earlier['reserve_missing'] = 0.0
            short_rows = []
        row['next_income_date'] = next_date
        row['next_income_is_estimated'] = next_estimated
        reserved_until = max(reserved_until, horizon) if reserved_until else horizon
        if row['reserve_missing'] > 0:
            short_rows.append(row)
        rows.append(row)

    uncovered = []
    awaiting = []
    for entry in obligations:
        if entry['remaining'] <= 0 or entry['next_month']:
            continue
        due_date = entry['item'].get('due_date')
        owner = _responsible_row(rows, due_date)
        if owner is None:
            continue
        next_date = owner['next_income_date']
        if next_date is not None and due_date is not None and due_date >= next_date:
            awaiting.append(_notice(
                entry, entry['remaining'], 'awaiting', next_date, owner['next_income_is_estimated'],
            ))
            continue
        notice = _notice(entry, entry['remaining'], 'insufficient', None, False)
        owner['shortfalls'].append(notice)
        uncovered.append(notice)

    return {
        'allocations': list(reversed(rows)),
        'shortfalls': sorted(uncovered, key=_by_due_date),
        'awaiting': sorted(awaiting, key=_by_due_date),
        'covered_by_previous': sorted(covered_by_previous, key=_by_due_date),
        'reserve_shortfall': _float(sum((_decimal(row['reserve_missing']) for row in rows), ZERO)),
    }


def window_daily_limit(allocations, today, spent, period_end):
    """Everyday money per day until the next income, for the incomes that cover ``today``.

    ``spent(start, today)`` returns the everyday expenses recorded in that range.
    """
    current = [
        row for row in allocations
        if row['date'] and row['date'] <= today and (row['next_income_date'] or period_end) > today
    ]
    if not current:
        return None
    end = min(row['next_income_date'] or period_end for row in current)
    start = min(row['date'] for row in current)
    living = _decimal(sum(
        (_decimal(item['amount']) for row in current for item in row['allocations'] if item['kind'] == 'living'),
        ZERO,
    ))
    spent_amount = _decimal(spent(start, today))
    available = _decimal(living - spent_amount)
    days_left = (end - today).days
    has_next = any(row['next_income_date'] == end for row in current)
    return {
        'start': start,
        'end': end if has_next else None,
        'end_is_estimated': any(
            row['next_income_is_estimated'] for row in current if row['next_income_date'] == end
        ),
        'living': _float(living),
        'spent': _float(spent_amount),
        'available': _float(available),
        'days_left': days_left,
        'daily': _float(max(available, ZERO) / days_left) if days_left > 0 else 0.0,
    }


def _cover_from_previous_month(obligations, payment_dates, period_start, first_income_date):
    """Dates before the month's first income that the previous month's income reached."""
    previous_payments = sorted((day for day in payment_dates if day < period_start), reverse=True)
    covered = []
    for entry in obligations:
        due_date = entry['item'].get('due_date')
        if entry['next_month'] or due_date is None or not period_start <= due_date < first_income_date:
            continue
        source = next(
            (
                payment for payment in previous_payments
                if due_date < (next_income_after(payment, payment_dates)[0] or date.max)
            ),
            None,
        )
        if source is not None:
            covered.append(_notice(entry, entry['remaining'], 'previous_period', source, False))
            entry['remaining'] = ZERO
    return covered


def _allocate(income, obligations, pools, day, horizon, ahead_until, reserve_needed):
    available = _decimal(income['amount'])
    rows = []

    def direct(entries, stage):
        nonlocal available
        for entry in entries:
            if available <= 0:
                return
            if entry['remaining'] <= 0:
                continue
            directed = _decimal(min(available, entry['remaining']))
            rows.append(_direction_row(entry, directed, day, stage))
            available = _decimal(available - directed)
            entry['remaining'] = _decimal(entry['remaining'] - directed)

    dated = [entry for entry in obligations if entry['item'].get('due_date') is not None]
    undated = [entry for entry in obligations if entry['item'].get('due_date') is None]
    direct([entry for entry in dated if entry['item']['due_date'] < day], 'overdue')
    direct([entry for entry in dated if day <= entry['item']['due_date'] < horizon] + undated, 'due')

    reserve = _decimal(min(available, reserve_needed))
    available = _decimal(available - reserve)
    direct([entry for entry in dated if horizon <= entry['item']['due_date'] < ahead_until], 'ahead')
    direct(pools, 'pool')
    living = _decimal(reserve + available)
    if living > 0:
        # Shown last although the reserve is kept before paying ahead.
        rows.append(_living_row(horizon, living, reserve))

    return {
        **income,
        'allocations': rows,
        'allocated_total': _float(sum((_decimal(row['amount']) for row in rows), ZERO)),
        'reserve': _float(reserve),
        'reserve_needed': _float(reserve_needed),
        'reserve_missing': _float(max(reserve_needed - reserve, ZERO)),
        'shortfalls': [],
    }


def _responsible_row(rows, due_date):
    """The income whose span holds the date: the latest one on or before it."""
    if not rows:
        return None
    if due_date is None:
        return rows[-1]
    owner = None
    for row in rows:
        if row['date'] <= due_date:
            owner = row
    return owner or rows[0]


def _reserve_needed(living_minimum, start, end):
    if living_minimum <= 0 or end <= start:
        return ZERO
    month_days = calendar.monthrange(start.year, start.month)[1]
    return _decimal(living_minimum * (end - start).days / month_days)


def _entry(item, amount, next_month=False):
    return {'item': item, 'initial': amount, 'remaining': amount, 'next_month': next_month}


def _obligation_order(entry):
    return (
        entry['item'].get('due_date') is None,
        entry['item'].get('due_date') or date.max,
        entry['item']['label'].lower(),
    )


def _living_row(horizon, amount, reserve):
    detail = f'на жизнь до {horizon.strftime("%d.%m.%Y")}'
    if ZERO < reserve < amount:
        detail = f'{detail} · из них минимум на жизнь {format_currency(reserve)}'
    return {
        'kind': 'living',
        'icon': 'bi-wallet2',
        'label': 'Повседневные расходы',
        'detail': detail,
        'amount': _float(amount),
        'due_date': None,
        'stage': 'living',
        'urgency': None,
    }


def _direction_row(entry, directed, income_date, stage):
    item = entry['item']
    allocated_before = _decimal(entry['initial'] - entry['remaining'])
    detail = item.get('base_detail', item['detail'])
    if item['kind'] == 'savings' and allocated_before > 0:
        detail = f'{detail} · ранее распределено {format_currency(allocated_before)}'
    due_date = item.get('due_date')
    if due_date is not None:
        detail = f'{_due_label(due_date, income_date, stage)} · {detail}'
    return {
        'kind': item['kind'],
        'icon': item['icon'],
        'label': item['label'],
        'detail': detail,
        'amount': _float(directed),
        'due_date': due_date,
        'stage': stage,
        'urgency': stage if stage in ('overdue', 'ahead') else _urgency(due_date, income_date),
    }


def _due_label(due_date, income_date, stage):
    formatted = due_date.strftime('%d.%m.%Y')
    if stage == 'overdue':
        return f'срок уже наступил: {formatted}'
    if stage == 'ahead':
        return f'можно оплатить заранее · срок {formatted}'
    if due_date == income_date:
        return 'оплатить сегодня'
    days = (due_date - income_date).days
    if days <= 7:
        return f'оплатить до {formatted} · через {days} дн.'
    return f'оплатить до {formatted}'


def _urgency(due_date, income_date):
    if due_date is None or income_date is None:
        return None
    if due_date == income_date:
        return 'today'
    if (due_date - income_date).days <= 7:
        return 'upcoming'
    return 'planned'


def _notice(entry, amount, reason, income_date, estimated):
    item = entry['item']
    return {
        'kind': item['kind'],
        'icon': item['icon'],
        'label': item['label'],
        'detail': item.get('detail'),
        'amount': _float(amount),
        'due_date': item.get('due_date'),
        'reason': reason,
        'expected_income_date': income_date,
        'expected_income_is_estimated': estimated,
        'prepaid': _float(entry['initial'] - _decimal(amount)) if reason == 'awaiting' else 0.0,
    }


def _by_due_date(item):
    return item['due_date'] or date.max, item['label'].lower()


def _month_end(period_start):
    return (period_start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _decimal(value):
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _float(value):
    return float(_decimal(value))
