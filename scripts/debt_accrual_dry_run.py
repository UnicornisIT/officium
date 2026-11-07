"""Show what the accrual calculation makes of every debt. Writes nothing.

For each debt prints the stored principal ("was") next to the computed
principal, interest, penalty and total debt:

  forward  - accruals from debt.accrual_start_date (the current cycle at
             release), as the application computes them;
  full     - the whole recorded payment history replayed from the cycle
             before the earliest payment, for comparison.

Run it against a migrated copy of the database:
    OFFICIUM_ENV=development DB_ENGINE=sqlite SQLITE_PATH=/abs/path/copy.db \\
        python scripts/debt_accrual_dry_run.py --as-of 2026-10-13 --penalty-rate 20
"""

import argparse
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def full_history_start(debt, payments):
    from app.services.debt_accrual_service import accrual_start_date
    from app.services.debt_calendar_service import previous_payment_due_date

    start = accrual_start_date(debt)
    earliest = min((payment.payment_date for payment in payments), default=None)
    if not debt.next_payment_date or not earliest or not start or earliest > start:
        return start
    due = debt.next_payment_date
    for _ in range(1200):
        if due < earliest:
            return due
        due = previous_payment_due_date(debt, due)
    return start


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--as-of', type=date.fromisoformat, default=date.today())
    parser.add_argument('--penalty-rate', type=Decimal, default=None,
                        help='use this penalty rate for debts other than split instead of the stored one')
    parser.add_argument('--debt-id', type=int)
    parser.add_argument('--start', type=date.fromisoformat, help='preview a confirmed accrual start date')
    parser.add_argument('--opening-principal', type=Decimal, help='preview a confirmed principal on --start')
    args = parser.parse_args()
    if (args.start is None) != (args.opening_principal is None):
        parser.error('--start and --opening-principal must be supplied together')
    if args.start is not None and args.debt_id is None:
        parser.error('select one debt with --debt-id for a reconciliation preview')
    if args.opening_principal is not None and (not args.opening_principal.is_finite() or args.opening_principal < 0):
        parser.error('--opening-principal must be a finite non-negative amount')

    from app import create_app
    from app.models import Debt, Payment
    from app.services.debt_accrual_service import DebtView, get_debt_state
    from extensions import db

    app = create_app()
    with app.app_context(), db.session.no_autoflush:
        print(f'Database: {db.engine.url.render_as_string(hide_password=True)}')
        print(f'As of {args.as_of}; penalty rate: {args.penalty_rate or "as stored"}\n')
        query = Debt.query.order_by(Debt.id)
        if args.debt_id is not None:
            query = query.filter_by(id=args.debt_id)
        for debt in query.all():
            payments = Payment.query.filter_by(debt_id=debt.id).all()
            overrides = {}
            if args.penalty_rate is not None and debt.debt_type != 'split':
                overrides['penalty_rate'] = args.penalty_rate
            print(f'#{debt.id} {debt.debt_type:<15} {debt.bank_name} / {debt.product_name} [{debt.status}]')
            print(f'   rate {debt.interest_rate}%, min {debt.minimum_payment}, first {debt.first_payment_amount}, '
                  f'next due {debt.next_payment_date}, accrual start {debt.accrual_start_date}, '
                  f'payments {len(payments)}')
            print(f'   {"WAS":<8} principal {Decimal(str(debt.remaining_amount)):>12}')
            forward = get_debt_state(DebtView(debt, **overrides), as_of=args.as_of, payments=payments)
            _print_state('FORWARD', forward)
            if args.start is not None:
                preview = get_debt_state(
                    DebtView(debt, accrual_start_date=args.start,
                             opening_principal_amount=args.opening_principal,
                             opening_interest_amount=Decimal('0'), opening_penalty_amount=Decimal('0'), **overrides),
                    as_of=args.as_of, payments=payments,
                )
                _print_state('PREVIEW', preview)
                for payment in sorted(payments, key=lambda p: (p.payment_date, p.id)):
                    allocation = preview['allocations'].get(payment.id)
                    if allocation:
                        print(f'   payment #{payment.id} {payment.payment_date}: amount {payment.amount}, '
                              f'interest {allocation["interest"]}, principal {allocation["principal"]}, '
                              f'balance {allocation["remaining_after"]} ({allocation["source"]})')
            full_start = full_history_start(debt, payments)
            if full_start != forward['accrual_start_date']:
                full = get_debt_state(
                    DebtView(debt, accrual_start_date=full_start, opening_principal_amount=None, **overrides),
                    as_of=args.as_of,
                    payments=payments,
                )
                _print_state('FULL', full)
            print()
        db.session.rollback()


def _print_state(label, state):
    line = (f'   {label:<8} principal {state["principal"]:>12}  interest {state["accrued_interest"]:>10}  '
            f'penalty {state["penalty"]:>9}  total {state["total"]:>12}  (from {state["accrual_start_date"]})')
    if state['is_overdue']:
        line += (f'\n            overdue: principal {state["overdue_principal"]}, interest {state["overdue_interest"]}, '
                 f'{state["overdue_days"]} d since {state["overdue_since"]}; '
                 f'to clear today {state["amount_to_clear_overdue"]}')
    print(line)
    for message in state['diagnostics']:
        print(f'   CHECK: {message}')


if __name__ == '__main__':
    main()
