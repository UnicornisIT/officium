from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP


MONEY = Decimal('0.01')


def calculate_period_interest(debt, principal_balance, period_start, period_end):
    total = sum(
        (segment['interest'] for segment in calculate_interest_segments(
            debt,
            principal_balance,
            period_start,
            period_end,
        )),
        Decimal('0.00'),
    )
    return total.quantize(MONEY, rounding=ROUND_HALF_UP)


def calculate_interest_segments(debt, principal_balance, period_start, period_end):
    """Return unrounded interest segments split by rate and calendar year."""
    if not period_start or not period_end or period_end <= period_start:
        return []

    effective_end = _effective_period_end(debt, period_start, period_end)
    if effective_end <= period_start:
        return []

    balance = Decimal(str(principal_balance or 0))
    if not balance.is_finite() or balance <= 0:
        return []

    segments = []
    cursor = period_start
    while cursor < effective_end:
        segment_end = _next_interest_segment_end(debt, cursor, effective_end)
        day_count = (segment_end - cursor).days
        rate = Decimal(str(debt.interest_rate_for(cursor) or 0))
        year_days = days_in_year(debt, cursor.year)
        interest = Decimal('0.00')
        if rate.is_finite() and rate > 0 and day_count > 0:
            interest = (
                balance
                * rate
                / Decimal('100')
                * Decimal(day_count)
                / Decimal(year_days)
            )
        segments.append({
            'start': cursor,
            'end': segment_end,
            'days': day_count,
            'rate': rate,
            'days_in_year': year_days,
            'interest': interest,
        })
        cursor = segment_end
    return segments


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


def days_in_year(debt, year):
    convention = getattr(debt, 'day_count_convention', 'actual_year') if debt is not None else 'actual_year'
    if convention == 'fixed_365':
        return 365
    if convention == 'fixed_366':
        return 366
    return 366 if _is_leap_year(year) else 365


def _is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
