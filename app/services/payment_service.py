from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from extensions import db
from app.models import Payment
from app.services.debt_accrual_service import accrual_start_date, get_debt_state
from app.services.debt_calendar_service import (
    previous_payment_due_date as _previous_payment_due_date,
    scheduled_payment_component as _scheduled_payment_component,
)
from app.time_utils import utc_now


MONEY = Decimal('0.01')
MAX_MONEY = Decimal('9999999999.99')
ZERO = Decimal('0.00')


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
    penalty_amount=None,
    bank_remaining_after_payment=None,
    manual_breakdown=None,
):
    if not payment_date:
        payment_date = date.today()
    amount = _money(amount, 'Сумма платежа')

    try:
        principal_before_payments = _principal_before_payments(debt)
        _pin_accrual_start(debt)
        previous_due_date = debt.next_payment_date
        payment = Payment(
            debt_id=debt.id,
            amount=amount,
            payment_date=payment_date,
            comment=comment,
            is_early_repayment=is_early_repayment,
            scheduled_payment_amount=scheduled_payment_amount,
            bank_remaining_after_payment=bank_remaining_after_payment,
            remaining_after_payment=ZERO,
        )
        _apply_breakdown_input(
            payment,
            principal_amount=principal_amount,
            interest_amount=interest_amount,
            fee_amount=fee_amount,
            penalty_amount=penalty_amount,
            manual_breakdown=manual_breakdown,
        )
        db.session.add(payment)
        db.session.flush()
        _recalculate(debt, principal_before_payments)
        payment.next_payment_date_advanced = bool(
            previous_due_date
            and debt.next_payment_date
            and debt.next_payment_date > previous_due_date
        )
        debt.updated_at = utc_now()
        db.session.commit()
    except ValueError:
        db.session.rollback()
        raise

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
    penalty_amount=None,
    bank_remaining_after_payment=None,
    manual_breakdown=None,
):
    if payment.debt_id != debt.id:
        raise ValueError('Платеж не найден')
    amount = _money(amount, 'Сумма платежа')

    try:
        principal_before_payments = _principal_before_payments(debt)
        _pin_accrual_start(debt)
        payment.amount = amount
        payment.payment_date = payment_date
        payment.comment = comment
        payment.is_early_repayment = is_early_repayment
        payment.scheduled_payment_amount = scheduled_payment_amount
        payment.bank_remaining_after_payment = bank_remaining_after_payment
        _apply_breakdown_input(
            payment,
            principal_amount=principal_amount,
            interest_amount=interest_amount,
            fee_amount=fee_amount,
            penalty_amount=penalty_amount,
            manual_breakdown=manual_breakdown,
        )
        _recalculate(debt, principal_before_payments)
        debt.updated_at = utc_now()
        db.session.commit()
    except ValueError:
        db.session.rollback()
        raise

    return payment


def delete_payment(debt, payment):
    if payment.debt_id != debt.id:
        raise ValueError('Платеж не найден')

    try:
        principal_before_payments = _principal_before_payments(debt)
        _pin_accrual_start(debt)
        db.session.delete(payment)
        db.session.flush()
        _recalculate(debt, principal_before_payments)
        debt.updated_at = utc_now()
        db.session.commit()
    except ValueError:
        db.session.rollback()
        raise


def _principal_before_payments(debt):
    """Principal before every recorded payment, from the stored balances."""
    payments = Payment.query.filter_by(debt_id=debt.id).all()
    return (
        Decimal(str(debt.remaining_amount or 0))
        + sum((_payment_principal(payment) for payment in payments), ZERO)
    ).quantize(MONEY)


def _pin_accrual_start(debt):
    # Without a stored start the fallback follows next_payment_date, which
    # payments move; pin it so recalculations stay deterministic.
    if getattr(debt, 'accrual_start_date', None) is None:
        debt.accrual_start_date = accrual_start_date(debt)


def _recalculate(debt, principal_before_payments):
    """Re-split every payment after the accrual start and refresh the debt."""
    payments = (
        Payment.query
        .filter_by(debt_id=debt.id)
        .order_by(Payment.payment_date.asc(), Payment.id.asc())
        .all()
    )
    start = debt.accrual_start_date
    settled = [payment for payment in payments if start and payment.payment_date <= start]
    tracked = [payment for payment in payments if not (start and payment.payment_date <= start)]

    # Payments before the accrual start are history: an automatic split
    # sends them to principal, as before.
    running = principal_before_payments
    for payment in settled:
        if _needs_settled_split(payment):
            payment.principal_amount = (_money(payment.amount, 'Сумма платежа') - _payment_fee(payment)).quantize(MONEY)
            payment.interest_amount = ZERO
            payment.penalty_amount = ZERO
        running = (running - _payment_principal(payment)).quantize(MONEY)
        if running < 0:
            raise ValueError('Сумма платежа превышает остаток долга')
        payment.remaining_after_payment = running

    # The engine takes the principal on the start date as remaining_amount
    # plus the stored principal of tracked payments.
    debt.remaining_amount = (running - sum((_payment_principal(p) for p in tracked), ZERO)).quantize(MONEY)
    today = date.today()
    last_payment_date = max((payment.payment_date for payment in payments), default=today)
    as_of = max(today, last_payment_date)
    state = get_debt_state(debt, as_of=as_of + timedelta(days=1), payments=payments)

    for payment in tracked:
        allocation = state['allocations'][payment.id]
        if payment.breakdown_source == 'manual':
            if allocation['principal'] < _payment_principal(payment):
                raise ValueError('Основной долг в платеже превышает текущий остаток долга')
        else:
            _raise_on_excess(payment, allocation)
            payment.principal_amount = allocation['principal']
            payment.interest_amount = allocation['interest']
            payment.penalty_amount = allocation['penalty']
        payment.remaining_after_payment = allocation['remaining_after']

    debt.remaining_amount = state['principal'] - state['capitalized_interest']

    if debt.is_payment_recurring and debt.next_payment_date:
        current = get_debt_state(debt, as_of=as_of, payments=payments)
        if current['next_due_date']:
            debt.next_payment_date = current['next_due_date']


def _needs_settled_split(payment):
    # A new or edited automatic payment carries an empty split; one that was
    # split while it was tracked keeps its parts when the start moves past it.
    return payment.breakdown_source != 'manual' and not any(
        Decimal(str(value or 0)) != 0
        for value in (payment.principal_amount, payment.interest_amount, payment.penalty_amount)
    )


def recalculate_debt(debt):
    """Re-split all payments after the debt settings changed. Does not commit."""
    principal_before_payments = _principal_before_payments(debt)
    _pin_accrual_start(debt)
    _recalculate(debt, principal_before_payments)


def accept_bank_remaining(debt, payment):
    """Turn the payment into a manual split that leaves the bank's balance."""
    if payment.debt_id != debt.id:
        raise ValueError('Платеж не найден')
    if payment.bank_remaining_after_payment is None:
        raise ValueError('Для платежа не указан остаток банка')

    try:
        principal_before_payments = _principal_before_payments(debt)
        _pin_accrual_start(debt)
        difference = (
            Decimal(str(payment.remaining_after_payment or 0))
            - Decimal(str(payment.bank_remaining_after_payment))
        ).quantize(MONEY)
        principal = (_payment_principal(payment) + difference).quantize(MONEY)
        available = (
            _money(payment.amount, 'Сумма платежа')
            - _payment_fee(payment)
            - Decimal(str(payment.penalty_amount or 0))
        ).quantize(MONEY)
        if principal < 0 or principal > available:
            raise ValueError('Остаток банка нельзя получить разбивкой этого платежа: проверьте сумму и дату')
        payment.breakdown_source = 'manual'
        payment.principal_amount = principal
        payment.interest_amount = (available - principal).quantize(MONEY)
        _recalculate(debt, principal_before_payments)
        debt.updated_at = utc_now()
        db.session.commit()
    except ValueError:
        db.session.rollback()
        raise
    return payment


def _raise_on_excess(payment, allocation):
    excess = allocation['excess']
    if excess <= 0:
        return
    available = _money(payment.amount, 'Сумма платежа') - _payment_fee(payment)
    if excess >= available:
        raise ValueError('Долг уже полностью погашен')
    if payment.is_early_repayment and not Decimal(str(payment.scheduled_payment_amount or 0)) > 0:
        raise ValueError('Сумма досрочного платежа превышает остаток долга')
    raise ValueError('Сумма платежа превышает остаток долга и начисленные проценты')


def _payment_principal(payment):
    if payment.principal_amount is not None:
        return Decimal(str(payment.principal_amount or 0)).quantize(MONEY)
    return Decimal(str(payment.amount or 0)).quantize(MONEY)


def _payment_fee(payment):
    return Decimal(str(payment.fee_amount or 0)).quantize(MONEY)


def _apply_breakdown_input(
    payment,
    principal_amount=None,
    interest_amount=None,
    fee_amount=None,
    penalty_amount=None,
    manual_breakdown=None,
):
    """Validate a breakdown typed in by the user, or mark the split automatic.

    manual_breakdown=None keeps the API behaviour: any principal, interest or
    penalty value means the breakdown is manual.
    """
    total = Decimal(str(payment.amount or 0)).quantize(MONEY)
    if total <= 0:
        raise ValueError('Сумма платежа должна быть больше нуля')

    principal = _optional_money(principal_amount)
    interest = _optional_money(interest_amount)
    penalty = _optional_money(penalty_amount)
    fee = _optional_money(fee_amount) or ZERO
    for value, label in (
        (principal, 'Основной долг'),
        (interest, 'Проценты'),
        (fee, 'Комиссии'),
        (penalty, 'Неустойка'),
    ):
        if value is not None and value < 0:
            raise ValueError(f'{label} не могут быть отрицательными')

    scheduled = _optional_money(payment.scheduled_payment_amount)
    if scheduled is not None and (scheduled < 0 or scheduled > total):
        raise ValueError('Обязательная часть должна быть от нуля до суммы платежа')
    if fee > total:
        raise ValueError('Комиссии не могут быть больше суммы платежа')
    payment.fee_amount = fee

    if manual_breakdown is None:
        manual_breakdown = any(value is not None for value in (principal, interest, penalty))
    if not manual_breakdown:
        payment.breakdown_source = 'auto'
        payment.principal_amount = ZERO
        payment.interest_amount = ZERO
        payment.penalty_amount = ZERO
        return

    penalty = penalty or ZERO
    amount_for_debt = (total - fee - penalty).quantize(MONEY)
    if amount_for_debt < 0:
        raise ValueError('Неустойка и комиссии не могут быть больше суммы платежа')
    if principal is not None and interest is not None:
        if (principal + interest + penalty + fee - total).copy_abs() != ZERO:
            raise ValueError('Основной долг, проценты, неустойка и комиссии должны совпадать с суммой платежа')
    elif principal is not None:
        if principal > amount_for_debt:
            raise ValueError('Основной долг не может быть больше суммы платежа')
        interest = (amount_for_debt - principal).quantize(MONEY)
    elif interest is not None:
        if interest > amount_for_debt:
            raise ValueError('Проценты не могут быть больше суммы платежа')
        principal = (amount_for_debt - interest).quantize(MONEY)
    else:
        raise ValueError('Для ручной разбивки укажите основной долг или проценты')

    payment.breakdown_source = 'manual'
    payment.principal_amount = principal.quantize(MONEY)
    payment.interest_amount = interest.quantize(MONEY)
    payment.penalty_amount = penalty.quantize(MONEY)


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
