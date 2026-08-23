from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta

from app.models import Payment
from app.services.debt_math_service import calculate_period_interest, interest_days


MAX_SCHEDULE_ROWS = 600
MONEY = Decimal('0.01')
SPLIT_INSTALLMENTS = 4


def build_debt_payment_schedule(debt, today=None):
    today = today or date.today()
    if not debt.next_payment_date:
        raise ValueError('Укажите дату следующего платежа, чтобы построить график.')

    if getattr(debt, 'debt_type', None) == 'split':
        return _build_split_payment_schedule(debt, today=today)

    monthly_payment = _schedule_monthly_payment(debt)
    if monthly_payment <= 0 and not (getattr(debt, 'repayment_type', 'annuity') == 'differentiated' and getattr(debt, 'loan_term_months', None)):
        raise ValueError('Укажите минимальный платеж, чтобы построить график.')

    remaining = Decimal(str(debt.remaining_amount or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)
    if remaining <= 0:
        return _schedule_result(debt, [], Decimal('0.00'), Decimal('0.00'))

    rows = []
    total_payments = Decimal('0.00')
    total_interest = Decimal('0.00')
    total_fees = Decimal('0.00')
    payment_date = debt.next_payment_date
    period_start = debt.interest_period_start_date or (payment_date - relativedelta(months=1))
    months_left = _schedule_term_months(debt, remaining)
    monthly_fee = Decimal(str(getattr(debt, 'monthly_fee_amount', 0) or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)

    for number in range(1, MAX_SCHEDULE_ROWS + 1):
        annual_rate = Decimal(str(debt.interest_rate_for(payment_date) or 0))
        interest = calculate_period_interest(
            debt,
            principal_balance=remaining,
            period_start=period_start,
            period_end=payment_date,
        ).quantize(MONEY, rounding=ROUND_HALF_UP)
        principal = _schedule_principal(
            debt,
            remaining=remaining,
            payment_without_fee=monthly_payment,
            interest=interest,
            months_left=months_left,
        )

        if principal <= 0:
            raise ValueError('Минимальный платеж не покрывает проценты. Увеличьте платеж или проверьте ставку.')

        payment_without_fee = (principal + interest).quantize(MONEY, rounding=ROUND_HALF_UP)
        payment_amount = (payment_without_fee + monthly_fee).quantize(MONEY, rounding=ROUND_HALF_UP)
        if principal >= remaining:
            principal = remaining
            payment_without_fee = (principal + interest).quantize(MONEY, rounding=ROUND_HALF_UP)
            payment_amount = (payment_without_fee + monthly_fee).quantize(MONEY, rounding=ROUND_HALF_UP)

        remaining = (remaining - principal).quantize(MONEY, rounding=ROUND_HALF_UP)
        total_payments += payment_amount
        total_interest += interest
        total_fees += monthly_fee

        rows.append({
            'number': number,
            'payment_date': payment_date.isoformat(),
            'payment_date_display': payment_date.strftime('%d.%m.%Y'),
            'payment': float(payment_amount),
            'payment_without_fee': float(payment_without_fee),
            'interest': float(interest),
            'principal': float(principal),
            'fee': float(monthly_fee),
            'remaining': float(remaining),
            'rate': float(annual_rate),
            'interest_days': interest_days(period_start, payment_date, debt=debt),
        })

        if remaining <= 0:
            break
        period_start = payment_date
        payment_date = payment_date + relativedelta(months=1)
        if months_left is not None:
            months_left = max(months_left - 1, 1)
    else:
        raise ValueError('График получился слишком длинным. Проверьте минимальный платеж и ставку.')

    return _schedule_result(debt, rows, total_payments, total_interest, total_fees)


def _build_split_payment_schedule(debt, today=None):
    remaining = Decimal(str(debt.remaining_amount or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)
    today = today or date.today()
    paid_payments = _split_paid_payments(debt, today=today)
    future_payments = _split_future_payments(debt, today=today)
    paid_total = sum((_payment_principal(payment) for payment in paid_payments), Decimal('0.00')).quantize(MONEY)
    future_total = sum((_payment_principal(payment) for payment in future_payments), Decimal('0.00')).quantize(MONEY)
    purchases = _split_purchases(debt)
    purchases_total = sum((Decimal(str(purchase.amount or 0)) for purchase in purchases), Decimal('0.00')).quantize(MONEY)

    if remaining <= 0 and not paid_payments and not future_payments and not purchases:
        return _schedule_result(debt, [], Decimal('0.00'), Decimal('0.00'), kind='split')

    rows = []
    payment_days = _split_payment_days(debt, paid_payments)
    outstanding_remaining = (remaining + future_total).quantize(MONEY, rounding=ROUND_HALF_UP)
    legacy_remaining = max(outstanding_remaining - purchases_total, Decimal('0.00')).quantize(MONEY, rounding=ROUND_HALF_UP) if purchases else outstanding_remaining
    running_remaining = (outstanding_remaining + paid_total).quantize(MONEY, rounding=ROUND_HALF_UP)

    for number, payment in enumerate(paid_payments, start=1):
        payment_amount = _payment_principal(payment)
        running_remaining = max(running_remaining - payment_amount, Decimal('0.00')).quantize(MONEY)

        rows.append({
            'number': number,
            'payment_date': payment.payment_date.isoformat(),
            'payment_date_display': payment.payment_date.strftime('%d.%m.%Y'),
            'payment': float(payment_amount),
            'payment_without_fee': float(payment_amount),
            'interest': 0.0,
            'principal': float(payment_amount),
            'fee': 0.0,
            'remaining': float(running_remaining),
            'rate': 0.0,
            'interest_days': 0,
            'status': 'paid',
            'status_label': 'оплачен',
        })

    planned_by_date = {}
    legacy_left = legacy_remaining
    last_known_legacy_date = None
    for payment in future_payments:
        payment_amount = min(_payment_principal(payment), legacy_left).quantize(MONEY, rounding=ROUND_HALF_UP)
        if payment_amount <= 0:
            continue
        _add_split_planned_amount(planned_by_date, payment.payment_date, payment_amount, 'Текущий сплит')
        legacy_left = (legacy_left - payment_amount).quantize(MONEY, rounding=ROUND_HALF_UP)
        last_known_legacy_date = payment.payment_date

    if legacy_left > 0:
        known_payments = paid_payments + future_payments
        installment_amount = _split_installment_amount(debt, legacy_left, known_payments)
        payment_date = (
            _next_split_cycle_date(last_known_legacy_date, payment_days)
            if last_known_legacy_date
            else _next_split_payment_date(debt, paid_payments, payment_days)
        )
        for payment_amount in _split_planned_installments(debt, legacy_left, installment_amount):
            _add_split_planned_amount(planned_by_date, payment_date, payment_amount, 'Текущий сплит')
            payment_date = _next_split_cycle_date(payment_date, payment_days)

    for purchase in purchases:
        purchase_date = purchase.purchase_date
        purchase_amounts = _split_equal_installments(
            Decimal(str(purchase.amount or 0)).quantize(MONEY, rounding=ROUND_HALF_UP),
            int(purchase.installments_count or SPLIT_INSTALLMENTS),
        )
        first_payment_reference = purchase_date + timedelta(days=14)
        payment_date = _next_split_cycle_date_after(first_payment_reference, payment_days, include_same=True)
        for payment_amount in purchase_amounts:
            _add_split_planned_amount(planned_by_date, payment_date, payment_amount, purchase.title or 'Покупка')
            payment_date = _next_split_cycle_date(payment_date, payment_days)

    planned_total = Decimal('0.00')
    planned_remaining = (legacy_remaining + purchases_total).quantize(MONEY, rounding=ROUND_HALF_UP)

    for payment_date in sorted(planned_by_date):
        item = planned_by_date[payment_date]
        payment_amount = min(item['amount'], planned_remaining).quantize(MONEY, rounding=ROUND_HALF_UP)
        planned_remaining = (planned_remaining - payment_amount).quantize(MONEY, rounding=ROUND_HALF_UP)
        planned_total += payment_amount

        status = 'overdue' if payment_date < today else 'planned'
        rows.append({
            'number': len(rows) + 1,
            'payment_date': payment_date.isoformat(),
            'payment_date_display': payment_date.strftime('%d.%m.%Y'),
            'payment': float(payment_amount),
            'payment_without_fee': float(payment_amount),
            'interest': 0.0,
            'principal': float(payment_amount),
            'fee': 0.0,
            'remaining': float(planned_remaining),
            'rate': 0.0,
            'interest_days': 0,
            'status': status,
            'status_label': 'просрочен' if status == 'overdue' else 'по плану',
            'components': item['components'],
        })

        if planned_remaining <= 0:
            break

    return _schedule_result(
        debt,
        rows,
        paid_total + planned_total,
        Decimal('0.00'),
        Decimal('0.00'),
        kind='split',
        interval_label='раз в 2 недели',
        total_paid=paid_total,
        total_planned=planned_total,
    )


def _split_paid_payments(debt, today=None):
    if not getattr(debt, 'id', None):
        return []
    today = today or date.today()
    return (
        Payment.query
        .filter(Payment.debt_id == debt.id, Payment.payment_date <= today)
        .order_by(Payment.payment_date.asc(), Payment.id.asc())
        .all()
    )


def _split_future_payments(debt, today=None):
    if not getattr(debt, 'id', None):
        return []
    today = today or date.today()
    return (
        Payment.query
        .filter(Payment.debt_id == debt.id, Payment.payment_date > today)
        .order_by(Payment.payment_date.asc(), Payment.id.asc())
        .all()
    )


def _split_purchases(debt):
    return sorted(
        list(getattr(debt, 'split_purchases', []) or []),
        key=lambda purchase: (purchase.purchase_date, purchase.id or 0),
    )


def _payment_principal(payment):
    value = payment.principal_amount if payment.principal_amount is not None else payment.amount
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _split_payment_days(debt, paid_payments):
    last_payment_date = max((payment.payment_date for payment in paid_payments), default=None)
    if debt.next_payment_date and (last_payment_date is None or debt.next_payment_date > last_payment_date):
        anchor_day = debt.next_payment_date.day
    elif last_payment_date:
        anchor_day = last_payment_date.day
    elif debt.next_payment_date:
        anchor_day = debt.next_payment_date.day
    else:
        anchor_day = date.today().day

    second_day = anchor_day + 15 if anchor_day <= 15 else anchor_day - 15
    return sorted({max(min(anchor_day, 31), 1), max(min(second_day, 31), 1)})


def _next_split_payment_date(debt, paid_payments, payment_days):
    last_payment_date = max((payment.payment_date for payment in paid_payments), default=None)
    if debt.next_payment_date and (last_payment_date is None or debt.next_payment_date > last_payment_date):
        return debt.next_payment_date

    base_date = last_payment_date or debt.next_payment_date
    if not base_date:
        return _next_split_cycle_date_after(date.today(), payment_days, include_same=True)
    return _next_split_cycle_date_after(base_date, payment_days)


def _next_split_cycle_date(current_date, payment_days):
    return _next_split_cycle_date_after(current_date, payment_days)


def _next_split_cycle_date_after(reference_date, payment_days, include_same=False):
    for month_offset in range(0, 36):
        month_start = date(reference_date.year, reference_date.month, 1) + relativedelta(months=month_offset)
        for day in payment_days:
            candidate = date(month_start.year, month_start.month, min(day, monthrange(month_start.year, month_start.month)[1]))
            if candidate > reference_date or (include_same and candidate == reference_date):
                return candidate
    raise ValueError('Не удалось построить календарь платежей Сплита.')


def _add_split_planned_amount(planned_by_date, payment_date, amount, title):
    amount = Decimal(str(amount or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)
    if amount <= 0:
        return
    if payment_date not in planned_by_date:
        planned_by_date[payment_date] = {'amount': Decimal('0.00'), 'components': []}
    planned_by_date[payment_date]['amount'] += amount
    planned_by_date[payment_date]['components'].append({
        'title': title,
        'amount': float(amount),
    })


def _split_installment_amount(debt, remaining, paid_payments):
    known_payment = Decimal(str(debt.minimum_payment or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)
    if known_payment > 0:
        return known_payment

    paid_amounts = [_payment_principal(payment) for payment in paid_payments if _payment_principal(payment) > 0]
    if paid_amounts:
        return paid_amounts[-1]

    if remaining <= 0:
        return Decimal('0.00')
    return (remaining / Decimal(SPLIT_INSTALLMENTS)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _split_planned_installments(debt, remaining, installment_amount):
    if remaining <= 0:
        return []

    if installment_amount > 0:
        payments = []
        left = remaining
        while left > installment_amount and len(payments) < MAX_SCHEDULE_ROWS:
            payments.append(installment_amount)
            left = (left - installment_amount).quantize(MONEY, rounding=ROUND_HALF_UP)
        if left > 0:
            if payments and left < (installment_amount * Decimal('0.10')):
                payments[-1] = (payments[-1] + left).quantize(MONEY, rounding=ROUND_HALF_UP)
            else:
                payments.append(left)
        return [payment for payment in payments if payment > 0]

    payments = []
    left = remaining
    for parts_left in range(SPLIT_INSTALLMENTS, 0, -1):
        payment = left if parts_left == 1 else (left / Decimal(parts_left)).quantize(MONEY, rounding=ROUND_HALF_UP)
        payments.append(payment)
        left = (left - payment).quantize(MONEY, rounding=ROUND_HALF_UP)
    return [payment for payment in payments if payment > 0]


def _split_equal_installments(amount, count):
    if amount <= 0 or count <= 0:
        return []
    if amount == amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP):
        payments = []
        left = amount
        regular_payment = (amount / Decimal(count)).to_integral_value(rounding=ROUND_CEILING).quantize(MONEY)
        for _ in range(count - 1):
            if left <= regular_payment:
                break
            payments.append(regular_payment)
            left = (left - regular_payment).quantize(MONEY, rounding=ROUND_HALF_UP)
        if left > 0:
            payments.append(left.quantize(MONEY, rounding=ROUND_HALF_UP))
        return [payment for payment in payments if payment > 0]

    payments = []
    left = amount
    for parts_left in range(count, 0, -1):
        payment = left if parts_left == 1 else (left / Decimal(parts_left)).quantize(MONEY, rounding=ROUND_HALF_UP)
        payments.append(payment)
        left = (left - payment).quantize(MONEY, rounding=ROUND_HALF_UP)
    return [payment for payment in payments if payment > 0]


def _schedule_monthly_payment(debt):
    base_payment = Decimal(str(debt.minimum_payment or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)
    if (
        getattr(debt, 'early_repayment_strategy', 'reduce_term') == 'reduce_payment'
        and getattr(debt, 'repayment_type', 'annuity') == 'annuity'
        and getattr(debt, 'loan_term_months', None)
        and Decimal(str(debt.remaining_amount or 0)) > 0
    ):
        recalculated = _annuity_payment(
            principal=Decimal(str(debt.remaining_amount or 0)),
            annual_rate=Decimal(str(debt.interest_rate_for(debt.next_payment_date) or 0)),
            months=int(debt.loan_term_months),
        )
        if recalculated > 0:
            return recalculated
    return base_payment


def _schedule_term_months(debt, remaining):
    term = getattr(debt, 'loan_term_months', None)
    if term and int(term) > 0:
        return int(term)
    monthly_payment = Decimal(str(debt.minimum_payment or 0))
    if monthly_payment > 0:
        return max(int((remaining / monthly_payment).to_integral_value(rounding=ROUND_HALF_UP)), 1)
    return None


def _schedule_principal(debt, remaining, payment_without_fee, interest, months_left):
    if getattr(debt, 'repayment_type', 'annuity') == 'differentiated' and months_left:
        return (remaining / Decimal(months_left)).quantize(MONEY, rounding=ROUND_HALF_UP)
    return (payment_without_fee - interest).quantize(MONEY, rounding=ROUND_HALF_UP)


def _annuity_payment(principal, annual_rate, months):
    principal = Decimal(str(principal or 0))
    if principal <= 0 or months <= 0:
        return Decimal('0.00')
    monthly_rate = Decimal(str(annual_rate or 0)) / Decimal('100') / Decimal('12')
    if monthly_rate <= 0:
        return (principal / Decimal(months)).quantize(MONEY, rounding=ROUND_HALF_UP)

    power = (Decimal('1') + monthly_rate) ** months
    payment = principal * monthly_rate * power / (power - Decimal('1'))
    return payment.quantize(MONEY, rounding=ROUND_HALF_UP)


def _schedule_result(
    debt,
    rows,
    total_payments,
    total_interest,
    total_fees=Decimal('0.00'),
    kind='credit',
    interval_label='раз в месяц',
    total_paid=Decimal('0.00'),
    total_planned=None,
):
    if total_planned is None:
        total_planned = total_payments
    return {
        'debt': debt.to_dict(),
        'kind': kind,
        'rows': rows,
        'total_payments': float(total_payments),
        'total_interest': float(total_interest),
        'total_fees': float(total_fees),
        'total_paid': float(total_paid),
        'total_planned': float(total_planned),
        'interval_label': interval_label,
        'months': len(rows),
    }
