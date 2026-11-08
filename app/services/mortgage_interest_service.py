from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.debt_accrual_service import (
    DebtView,
    _load_payments,
    calculate_accrued_interest,
    opening_principal,
)
from app.services.debt_schedule_service import build_debt_payment_schedule


def mortgage_month_interest(debts, month_start, month_end, today):
    total = Decimal('0.00')
    for debt in debts:
        if month_start <= today:
            total += calculate_accrued_interest(
                debt, month_start, min(month_end, today)
            )
            continue

        payments = _load_payments(debt)
        # Planned payments must not change the legacy reconstructed opening balance.
        view = DebtView(
            debt, opening_principal_amount=opening_principal(debt, payments)
        )
        try:
            schedule = build_debt_payment_schedule(debt, today=today)
        except (ValueError, ArithmeticError):
            return None
        known_dates = {
            payment.payment_date for payment in payments if payment.payment_date > today
        }
        for index, row in enumerate(schedule['rows'], 1):
            payment_date = max(today, date.fromisoformat(row['payment_date']))
            if payment_date >= month_end or payment_date in known_dates:
                continue
            payments.append(
                SimpleNamespace(
                    id=-index,
                    payment_date=payment_date,
                    amount=Decimal(str(row['payment'])),
                    principal_amount=Decimal(str(row['principal'])),
                    interest_amount=Decimal(str(row['interest'])),
                    penalty_amount=Decimal(str(row.get('penalty', 0))),
                    fee_amount=Decimal(str(row.get('fee', 0))),
                    breakdown_source='manual',
                    is_early_repayment=False,
                    scheduled_payment_amount=None,
                )
            )
        total += calculate_accrued_interest(
            view, month_start, month_end, payments=payments
        )
    return total
