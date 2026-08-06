from datetime import date
from decimal import Decimal


def interest_rate_for_date(debt, value_date=None):
    check_date = value_date or date.today()
    return debt.interest_rate_for(check_date)


def calculate_overdue_interest(debt, today=None):
    today = today or date.today()
    if not debt.next_payment_date or debt.next_payment_date >= today:
        return None

    remaining_amount = Decimal(str(debt.remaining_amount or 0))
    periods = _overdue_interest_periods(debt, debt.next_payment_date, today)
    if not periods:
        return None

    total_interest = sum(period['interest'] for period in periods)
    effective_rate = Decimal(str(periods[-1]['rate']))
    daily_rate = effective_rate / Decimal('365')
    interest_per_day = remaining_amount * daily_rate / Decimal('100')

    return {
        'annual_rate': float(effective_rate),
        'daily_rate': float(daily_rate),
        'interest_per_day': float(interest_per_day),
        'total_overdue_interest': float(total_interest),
        'total_with_overdue': float(remaining_amount + total_interest),
        'periods': [
            {
                'start': period['start'],
                'end': period['end'],
                'days': period['days'],
                'rate': float(period['rate']),
                'interest': float(period['interest']),
            }
            for period in periods
        ],
    }


def _overdue_interest_periods(debt, start_date, end_date):
    remaining_amount = Decimal(str(debt.remaining_amount or 0))
    change_date = debt.interest_rate_change_date
    boundaries = [start_date]
    if change_date and start_date < change_date < end_date:
        boundaries.append(change_date)
    boundaries.append(end_date)

    periods = []
    for start, end in zip(boundaries, boundaries[1:]):
        days = (end - start).days
        if days <= 0:
            continue
        rate = interest_rate_for_date(debt, start)
        if rate is None:
            continue
        rate = Decimal(str(rate))
        interest = remaining_amount * (rate / Decimal('365')) / Decimal('100') * Decimal(days)
        periods.append({
            'start': start,
            'end': end,
            'days': days,
            'rate': rate,
            'interest': interest,
        })
    return periods
