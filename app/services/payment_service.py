from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta
from sqlalchemy import case, func, or_

from extensions import db
from app.models import Payment
from app.services.debt_math_service import calculate_period_interest


MONEY = Decimal('0.01')
MAX_MONEY = Decimal('9999999999.99')
SPLIT_INSTALLMENTS = 4


def add_payment(
    debt,
    amount,
    payment_date=None,
    comment=None,
    is_early_repayment=False,
    scheduled_payment_amount=None,
    principal_amount=None,
    interest_amount=None,
    fee_amount=None,
    bank_remaining_after_payment=None,
):
    if not payment_date:
        payment_date = date.today()
    amount = _money(amount, 'Сумма платежа')

    opening_balance = _opening_balance_for_recalculation(debt)
    remaining_before_payment = Decimal(str(debt.remaining_amount or 0)).quantize(MONEY)
    principal_amount, interest_amount, fee_amount = _resolve_payment_breakdown(
        debt,
        amount=amount,
        payment_date=payment_date,
        is_early_repayment=is_early_repayment,
        scheduled_payment_amount=scheduled_payment_amount,
        principal_amount=principal_amount,
        interest_amount=interest_amount,
        fee_amount=fee_amount,
        remaining_before_payment=remaining_before_payment,
    )
    required_payment = _required_payment_amount(debt, remaining_before_payment)
    payment = Payment(
        debt_id=debt.id,
        amount=amount,
        principal_amount=principal_amount,
        interest_amount=interest_amount,
        fee_amount=fee_amount,
        payment_date=payment_date,
        comment=comment,
        is_early_repayment=is_early_repayment,
        scheduled_payment_amount=scheduled_payment_amount,
        remaining_after_payment=max(remaining_before_payment - principal_amount, Decimal('0.00')),
        bank_remaining_after_payment=bank_remaining_after_payment,
    )

    debt.updated_at = datetime.utcnow()
    db.session.add(payment)
    db.session.flush()
    _recalculate_payment_balances(debt, opening_balance)
    payment.next_payment_date_advanced = False
    if not is_early_repayment or Decimal(str(scheduled_payment_amount or 0)) > 0:
        payment.next_payment_date_advanced = _advance_recurring_payment_date_if_covered(
            debt,
            payment_date=payment_date,
            required_payment=required_payment,
        )
    db.session.commit()

    return payment


def update_payment(
    debt,
    payment,
    amount,
    payment_date,
    comment=None,
    is_early_repayment=False,
    scheduled_payment_amount=None,
    principal_amount=None,
    interest_amount=None,
    fee_amount=None,
    bank_remaining_after_payment=None,
):
    if payment.debt_id != debt.id:
        raise ValueError('Платеж не найден')
    amount = _money(amount, 'Сумма платежа')

    original_payment_date = payment.payment_date
    opening_balance = _opening_balance_for_recalculation(debt)

    payment.amount = amount
    payment.payment_date = payment_date
    payment.comment = comment
    payment.is_early_repayment = is_early_repayment
    payment.scheduled_payment_amount = scheduled_payment_amount
    payment.bank_remaining_after_payment = bank_remaining_after_payment
    remaining_before_payment = _balance_before_payment(debt, payment, opening_balance)
    payment.principal_amount, payment.interest_amount, payment.fee_amount = _resolve_payment_breakdown(
        debt,
        amount=amount,
        payment_date=payment_date,
        is_early_repayment=is_early_repayment,
        scheduled_payment_amount=scheduled_payment_amount,
        principal_amount=principal_amount,
        interest_amount=interest_amount,
        fee_amount=fee_amount,
        remaining_before_payment=remaining_before_payment,
        exclude_payment_id=payment.id,
    )

    _recalculate_payment_balances(debt, opening_balance)
    _sync_recurring_payment_date_after_edit(debt, payment.payment_date or original_payment_date)
    debt.updated_at = datetime.utcnow()
    db.session.commit()

    return payment


def _opening_balance_for_recalculation(debt):
    payments = Payment.query.filter_by(debt_id=debt.id).all()
    principal_paid_total = sum((_payment_principal(payment) for payment in payments), Decimal('0'))
    return (Decimal(str(debt.remaining_amount or 0)) + principal_paid_total).quantize(MONEY)


def _recalculate_payment_balances(debt, opening_balance):
    remaining = Decimal(str(opening_balance or 0)).quantize(MONEY)
    payments = Payment.query.filter_by(debt_id=debt.id).order_by(Payment.payment_date.asc(), Payment.id.asc()).all()

    for payment in payments:
        principal_amount = _payment_principal(payment)
        remaining = max(remaining - principal_amount, Decimal('0.00')).quantize(MONEY)
        payment.remaining_after_payment = remaining

    debt.remaining_amount = remaining


def _balance_before_payment(debt, target_payment, opening_balance):
    remaining = Decimal(str(opening_balance or 0)).quantize(MONEY)
    payments = Payment.query.filter_by(debt_id=debt.id).order_by(Payment.payment_date.asc(), Payment.id.asc()).all()
    for payment in payments:
        if payment.id == target_payment.id:
            return remaining
        remaining = max(remaining - _payment_principal(payment), Decimal('0.00')).quantize(MONEY)
    return remaining


def _payment_principal(payment):
    if payment.principal_amount is not None:
        return Decimal(str(payment.principal_amount or 0)).quantize(MONEY)
    return Decimal(str(payment.amount or 0)).quantize(MONEY)


def _resolve_payment_breakdown(
    debt,
    amount,
    payment_date,
    is_early_repayment,
    scheduled_payment_amount=None,
    principal_amount=None,
    interest_amount=None,
    fee_amount=None,
    remaining_before_payment=None,
    exclude_payment_id=None,
):
    total = Decimal(str(amount or 0)).quantize(MONEY)
    if total <= 0:
        raise ValueError('Сумма платежа должна быть больше нуля')

    principal_was_supplied = principal_amount is not None and str(principal_amount).strip() != ''
    interest_was_supplied = interest_amount is not None and str(interest_amount).strip() != ''
    principal = _optional_money(principal_amount)
    interest = _optional_money(interest_amount)
    fee = _optional_money(fee_amount) or Decimal('0.00')
    for value, label in (
        (principal, 'Основной долг'),
        (interest, 'Проценты'),
        (fee, 'Комиссии'),
    ):
        if value is not None and value < 0:
            raise ValueError(f'{label} не могут быть отрицательными')

    scheduled = _optional_money(scheduled_payment_amount)
    if scheduled is not None and (scheduled < 0 or scheduled > total):
        raise ValueError('Обязательная часть должна быть от нуля до суммы платежа')
    if fee > total:
        raise ValueError('Комиссии не могут быть больше суммы платежа')
    amount_for_debt = (total - fee).quantize(MONEY)
    remaining = Decimal(str(remaining_before_payment or 0)).quantize(MONEY)
    if remaining <= 0:
        raise ValueError('Долг уже полностью погашен')

    if principal is not None and interest is not None:
        if (principal + interest + fee - total).copy_abs() != Decimal('0.00'):
            raise ValueError('Основной долг, проценты и комиссии должны совпадать с суммой платежа')
    elif principal is not None:
        if principal > amount_for_debt:
            raise ValueError('Основной долг не может быть больше суммы платежа')
        interest = (amount_for_debt - principal).quantize(MONEY)
    elif interest is not None:
        if interest > amount_for_debt:
            raise ValueError('Проценты не могут быть больше суммы платежа')
        principal = (amount_for_debt - interest).quantize(MONEY)
    elif is_early_repayment and not Decimal(str(scheduled_payment_amount or 0)) > 0:
        if amount_for_debt > remaining:
            raise ValueError('Сумма досрочного платежа превышает остаток долга')
        principal = amount_for_debt
        interest = Decimal('0.00')
    else:
        if getattr(debt, 'is_first_payment_pending', lambda: False)():
            estimated_interest = _estimate_first_payment_interest(
                debt,
                payment_date=payment_date,
                remaining_before_payment=remaining_before_payment,
            )
        else:
            estimated_interest = _estimate_payment_interest(
                debt,
                payment_date=payment_date,
                remaining_before_payment=remaining_before_payment,
                exclude_payment_id=exclude_payment_id,
            )
        if amount_for_debt > remaining + estimated_interest:
            raise ValueError('Сумма платежа превышает остаток долга и начисленные проценты')
        interest = min(estimated_interest, amount_for_debt).quantize(MONEY)
        principal = (amount_for_debt - interest).quantize(MONEY)

    if principal > remaining and (principal_was_supplied or interest_was_supplied):
        raise ValueError('Основной долг в платеже превышает текущий остаток долга')
    if principal > remaining:
        principal = remaining
        interest = (amount_for_debt - principal).quantize(MONEY)

    return principal.quantize(MONEY), interest.quantize(MONEY), fee.quantize(MONEY)


def _optional_money(value):
    if value is None or str(value).strip() == '':
        return None
    return _money(value, 'Сумма')


def _money(value, label):
    try:
        result = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except Exception as exc:
        raise ValueError(f'{label} содержит некорректное число') from exc
    if not result.is_finite():
        raise ValueError(f'{label} содержит некорректное число')
    if result.copy_abs() > MAX_MONEY:
        raise ValueError(f'{label} превышает максимально допустимое значение')
    return result


def _estimate_payment_interest(
    debt,
    payment_date,
    remaining_before_payment,
    exclude_payment_id=None,
):
    if not payment_date:
        return Decimal('0.00')
    if getattr(debt, 'debt_type', None) == 'split':
        return Decimal('0.00')
    due_date = getattr(debt, 'next_payment_date', None)
    if not due_date or payment_date <= due_date:
        return Decimal('0.00')

    payments = Payment.query.filter(
        Payment.debt_id == debt.id,
        Payment.payment_date >= due_date,
        Payment.payment_date <= payment_date,
    )
    if exclude_payment_id is not None:
        payments = payments.filter(Payment.id != exclude_payment_id)
    payments = payments.order_by(Payment.payment_date.asc(), Payment.id.asc()).all()

    opening_balance = Decimal(str(remaining_before_payment or 0)).quantize(MONEY)
    opening_balance += sum((_payment_principal(item) for item in payments), Decimal('0.00'))
    balance = opening_balance
    cursor = due_date
    accrued = Decimal('0.00')
    interest_paid = Decimal('0.00')

    for prior_payment in payments:
        segment_end = prior_payment.payment_date
        if segment_end > cursor:
            accrued += calculate_period_interest(
                debt,
                principal_balance=balance,
                period_start=cursor,
                period_end=segment_end,
            )
        balance = max(balance - _payment_principal(prior_payment), Decimal('0.00'))
        if prior_payment.payment_date > due_date:
            interest_paid += Decimal(str(prior_payment.interest_amount or 0)).quantize(MONEY)
        cursor = segment_end
        if getattr(debt, 'include_payment_day', False):
            cursor += relativedelta(days=1)

    if payment_date > cursor:
        accrued += calculate_period_interest(
            debt,
            principal_balance=balance,
            period_start=cursor,
            period_end=payment_date,
        )
    return max(accrued - interest_paid, Decimal('0.00')).quantize(MONEY, rounding=ROUND_HALF_UP)


def _estimate_first_payment_interest(debt, payment_date, remaining_before_payment):
    due_date = getattr(debt, 'next_payment_date', None) or payment_date
    period_end = max(payment_date, due_date)
    period_start = getattr(debt, 'interest_period_start_date', None) or (due_date - relativedelta(months=1))
    return calculate_period_interest(
        debt,
        principal_balance=remaining_before_payment,
        period_start=period_start,
        period_end=period_end,
    )


def _interest_period_start(debt, payment_date):
    if getattr(debt, 'interest_period_start_date', None):
        return debt.interest_period_start_date
    if debt.next_payment_date:
        due_date = debt.next_payment_date
        while payment_date > due_date:
            due_date = due_date + relativedelta(months=1)
        return due_date - relativedelta(months=1)
    return payment_date - relativedelta(months=1)


def _sync_recurring_payment_date_after_edit(debt, edited_payment_date):
    if not debt.is_payment_recurring or not debt.next_payment_date or not edited_payment_date:
        return

    if _cycle_is_covered(debt, debt.next_payment_date):
        debt.next_payment_date = _next_payment_due_date(debt, debt.next_payment_date)
        return

    previous_due_date = _previous_payment_due_date(debt, debt.next_payment_date)
    previous_cycle_start = _previous_payment_due_date(debt, previous_due_date)
    if previous_cycle_start <= edited_payment_date <= debt.next_payment_date:
        if not _cycle_is_covered(debt, previous_due_date):
            debt.next_payment_date = previous_due_date


def _required_payment_amount(debt, remaining_before_payment):
    if getattr(debt, 'is_first_payment_pending', lambda: False)():
        first_payment = Decimal(str(debt.first_payment_amount or 0))
        if first_payment > 0:
            return first_payment
    minimum_payment = Decimal(str(debt.minimum_payment or 0))
    if minimum_payment > 0:
        return min(minimum_payment, remaining_before_payment)
    if getattr(debt, 'debt_type', None) == 'split':
        return min(_split_default_payment_amount(remaining_before_payment), remaining_before_payment)
    return Decimal('0.01')


def _advance_recurring_payment_date_if_covered(debt, payment_date, required_payment):
    if not debt.is_payment_recurring or not debt.next_payment_date or required_payment <= 0:
        return False

    due_date = debt.next_payment_date
    cycle_start = _previous_payment_due_date(debt, due_date)
    if payment_date <= cycle_start:
        return False

    paid_in_cycle = db.session.query(func.coalesce(func.sum(case(
        (Payment.is_early_repayment.is_(False), Payment.amount),
        else_=func.coalesce(Payment.scheduled_payment_amount, 0),
    )), 0)).filter(
        Payment.debt_id == debt.id,
        Payment.payment_date > cycle_start,
        Payment.payment_date <= payment_date,
    ).scalar()

    if Decimal(str(paid_in_cycle or 0)) < required_payment:
        return False

    debt.next_payment_date = _next_payment_due_date(debt, due_date)
    return True


def _cycle_is_covered(debt, due_date):
    required_payment = _required_payment_for_cycle(debt, due_date)
    if required_payment <= 0 and getattr(debt, 'debt_type', None) == 'split':
        required_payment = _split_default_payment_amount(debt.remaining_amount)
    if required_payment <= 0:
        return False

    cycle_start = _previous_payment_due_date(debt, due_date)
    paid_in_cycle = db.session.query(func.coalesce(func.sum(case(
        (Payment.is_early_repayment.is_(False), Payment.amount),
        else_=func.coalesce(Payment.scheduled_payment_amount, 0),
    )), 0)).filter(
        Payment.debt_id == debt.id,
        Payment.payment_date > cycle_start,
        Payment.payment_date <= due_date,
    ).scalar()

    return Decimal(str(paid_in_cycle or 0)).quantize(MONEY) >= required_payment


def _required_payment_for_cycle(debt, due_date):
    first_payment = Decimal(str(getattr(debt, 'first_payment_amount', 0) or 0)).quantize(MONEY)
    if first_payment > 0:
        cycle_start = _previous_payment_due_date(debt, due_date)
        earlier_required_payment = Payment.query.filter(
            Payment.debt_id == debt.id,
            or_(
                Payment.is_early_repayment.is_(False),
                Payment.scheduled_payment_amount > 0,
            ),
            Payment.payment_date <= cycle_start,
        ).first()
        if earlier_required_payment is None:
            return first_payment
    return Decimal(str(debt.minimum_payment or 0)).quantize(MONEY)


def paid_toward_payment_cycle(debt, due_date, through_date=None, payments=None):
    """Return amounts covering the required part of one payment cycle."""
    if not due_date:
        return Decimal('0.00')

    cycle_start = _previous_payment_due_date(debt, due_date)
    through_date = through_date or due_date
    if payments is None:
        payments = Payment.query.filter_by(debt_id=debt.id).all() if getattr(debt, 'id', None) else []

    paid = sum(
        (
            _scheduled_payment_component(payment)
            for payment in payments
            if (
                payment.payment_date
                and cycle_start < payment.payment_date <= through_date
            )
        ),
        Decimal('0.00'),
    )
    return paid.quantize(MONEY, rounding=ROUND_HALF_UP)


def _scheduled_payment_component(payment):
    if not getattr(payment, 'is_early_repayment', False):
        return Decimal(str(payment.amount or 0))
    return Decimal(str(getattr(payment, 'scheduled_payment_amount', 0) or 0))


def principal_paid_in_payment_cycle(debt, due_date, through_date=None, payments=None):
    """Return principal reduction recorded during a required-payment cycle."""
    if not due_date:
        return Decimal('0.00')

    cycle_start = _previous_payment_due_date(debt, due_date)
    through_date = through_date or due_date
    if payments is None:
        payments = Payment.query.filter_by(debt_id=debt.id).all() if getattr(debt, 'id', None) else []

    principal = sum(
        (
            Decimal(str(
                payment.principal_amount
                if payment.principal_amount is not None
                else payment.amount or 0
            ))
            for payment in payments
            if payment.payment_date and cycle_start < payment.payment_date <= through_date
        ),
        Decimal('0.00'),
    )
    return principal.quantize(MONEY, rounding=ROUND_HALF_UP)


def _next_payment_due_date(debt, due_date):
    if getattr(debt, 'debt_type', None) == 'split':
        return _next_split_cycle_date(due_date)
    return due_date + relativedelta(months=1)


def _previous_payment_due_date(debt, due_date):
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


def _split_default_payment_amount(remaining):
    remaining = Decimal(str(remaining or 0)).quantize(MONEY)
    if remaining <= 0:
        return Decimal('0.00')
    return (remaining / Decimal(SPLIT_INSTALLMENTS)).quantize(MONEY, rounding=ROUND_HALF_UP)
