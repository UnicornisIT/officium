"""Payment due-date calendar shared by payments and accruals."""

from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta


MONEY = Decimal('0.01')
SPLIT_INSTALLMENTS = 4


def scheduled_payment_component(payment):
    if not getattr(payment, 'is_early_repayment', False):
        return Decimal(str(payment.amount or 0))
    return Decimal(str(getattr(payment, 'scheduled_payment_amount', 0) or 0))


def next_payment_due_date(debt, due_date):
    if getattr(debt, 'debt_type', None) == 'split':
        return _next_split_cycle_date(due_date)
    return due_date + relativedelta(months=1)


def previous_payment_due_date(debt, due_date):
    if getattr(debt, 'debt_type', None) == 'split':
        return _previous_split_cycle_date(due_date)
    return due_date - relativedelta(months=1)


def _split_payment_days(anchor_date):
    anchor_day = anchor_date.day
    second_day = anchor_day + 15 if anchor_day <= 15 else anchor_day - 15
    return sorted({max(min(anchor_day, 31), 1), max(min(second_day, 31), 1)})


def _next_split_cycle_date(current_date):
    payment_days = _split_payment_days(current_date)
    for month_offset in range(0, 24):
        month_start = date(current_date.year, current_date.month, 1) + relativedelta(months=month_offset)
        for day in payment_days:
            candidate = date(month_start.year, month_start.month, min(day, monthrange(month_start.year, month_start.month)[1]))
            if candidate > current_date:
                return candidate
    return current_date + relativedelta(days=14)


def _previous_split_cycle_date(current_date):
    payment_days = _split_payment_days(current_date)
    for month_offset in range(0, -24, -1):
        month_start = date(current_date.year, current_date.month, 1) + relativedelta(months=month_offset)
        for day in sorted(payment_days, reverse=True):
            candidate = date(month_start.year, month_start.month, min(day, monthrange(month_start.year, month_start.month)[1]))
            if candidate < current_date:
                return candidate
    return current_date - relativedelta(days=14)


def split_default_payment_amount(remaining):
    remaining = Decimal(str(remaining or 0)).quantize(MONEY)
    if remaining <= 0:
        return Decimal('0.00')
    return (remaining / Decimal(SPLIT_INSTALLMENTS)).quantize(MONEY, rounding=ROUND_HALF_UP)
