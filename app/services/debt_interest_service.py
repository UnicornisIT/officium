from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.services.debt_math_service import (
    calculate_interest_segments,
    calculate_period_interest,
)


MONEY = Decimal('0.01')


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

    total_interest = calculate_period_interest(
        debt,
        principal_balance=remaining_amount,
        period_start=debt.next_payment_date,
        period_end=today,
    )
    effective_rate = Decimal(str(periods[-1]['rate']))
    year_days = Decimal(periods[-1]['days_in_year'])
    daily_rate = effective_rate / year_days
    interest_per_day = (
        remaining_amount * daily_rate / Decimal('100')
    ).quantize(MONEY, rounding=ROUND_HALF_UP)

    rounded_periods = []
    rounded_so_far = Decimal('0.00')
    for index, period in enumerate(periods):
        if index == len(periods) - 1:
            interest = total_interest - rounded_so_far
        else:
            interest = period['interest'].quantize(MONEY, rounding=ROUND_HALF_UP)
            rounded_so_far += interest
        rounded_periods.append({**period, 'interest': interest})

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
                'days_in_year': period['days_in_year'],
            }
            for period in rounded_periods
        ],
    }


def _overdue_interest_periods(debt, start_date, end_date):
    remaining_amount = Decimal(str(debt.remaining_amount or 0))
    return [
        {**segment, 'end': min(segment['end'], end_date)}
        for segment in calculate_interest_segments(
            debt,
            remaining_amount,
            start_date,
            end_date,
        )
        if segment['rate'] > 0 and segment['days'] > 0
    ]
