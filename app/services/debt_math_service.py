from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP


MONEY = Decimal('0.01')


def calculate_period_interest(debt, principal_balance, period_start, period_end):
    if not period_start or not period_end or period_end <= period_start:
        return Decimal('0.00')

    effective_end = _effective_period_end(debt, period_start, period_end)
    if effective_end <= period_start:
        return Decimal('0.00')

    balance = Decimal(str(principal_balance or 0))
    if balance <= 0:
        return Decimal('0.00')

    total = Decimal('0.00')
    cursor = period_start
    while cursor < effective_end:
        segment_end = _next_interest_segment_end(debt, cursor, effective_end)
        days = Decimal((segment_end - cursor).days)
        rate = Decimal(str(debt.interest_rate_for(cursor) or 0))
        if rate > 0 and days > 0:
            total += balance * rate / Decimal('100') * days / Decimal(_days_in_year(debt, cursor.year))
        cursor = segment_end

    return total.quantize(MONEY, rounding=ROUND_HALF_UP)


def interest_days(period_start, period_end, debt=None):
    if not period_start or not period_end or period_end <= period_start:
        return 0
    effective_end = _effective_period_end(debt, period_start, period_end)
    return max((effective_end - period_start).days, 0)


def _next_interest_segment_end(debt, cursor, period_end):
    candidates = [period_end, date(cursor.year + 1, 1, 1)]
    change_date = getattr(debt, 'interest_rate_change_date', None)
    if change_date and cursor < change_date < period_end:
        candidates.append(change_date)
    return min(candidates)


def _effective_period_end(debt, period_start, period_end):
    if debt is not None and getattr(debt, 'include_payment_day', False):
        return period_end + timedelta(days=1)
    return period_end


def _days_in_year(debt, year):
    convention = getattr(debt, 'day_count_convention', 'actual_year') if debt is not None else 'actual_year'
    if convention == 'fixed_365':
        return 365
    if convention == 'fixed_366':
        return 366
    return 366 if _is_leap_year(year) else 365


def _is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
