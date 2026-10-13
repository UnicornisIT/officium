"""Deterministic debt state: principal, interest, overdue and penalty.

Everything is derived from the debt settings and its payment history, so
editing or deleting a payment in the past and calling get_debt_state again
gives the corrected picture. Nothing is accrued by a scheduler.

Conventions:
- accruals start on debt.accrual_start_date; payments on or before that
  date are settled history and are already reflected in remaining_amount;
- the principal on that date is remaining_amount plus the principal parts
  of later payments, so remaining_amount must be stored as the computed
  principal minus capitalized interest;
- a payment cycle is (previous due date, due date]; a cycle is paid on time
  when the required part of its payments covers the required payment;
- interest and penalty accrue over half-open day ranges [start, end), so a
  state "as of" a date includes everything through the previous day;
- the unpaid part of a required payment becomes overdue the day after the
  due date: overdue interest first, then overdue principal;
- a payment goes to overdue interest, overdue principal, penalty, current
  interest and principal, in that order (353-FZ art. 5 part 20); a manual
  breakdown entered by the user is applied as entered.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from dateutil.relativedelta import relativedelta
from sqlalchemy import inspect as sa_inspect

from app.services.debt_calendar_service import (
    next_payment_due_date,
    previous_payment_due_date,
    scheduled_payment_component,
    split_default_payment_amount,
)
from app.services.debt_math_service import calculate_interest_segments, days_in_year


MONEY = Decimal('0.01')
ZERO = Decimal('0.00')
MAX_CYCLES = 1200


def money(value):
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


@dataclass
class _OverdueItem:
    due_date: date
    interest: Decimal
    principal: Decimal


@dataclass
class _Ledger:
    principal: Decimal
    cursor: date
    current_interest: Decimal = Decimal('0')
    grace_interest: Decimal = Decimal('0')
    capitalized_interest: Decimal = ZERO
    penalty: Decimal = Decimal('0')
    overdue: list = field(default_factory=list)

    @property
    def overdue_interest(self):
        return sum((item.interest for item in self.overdue), ZERO)

    @property
    def overdue_principal(self):
        return sum((item.principal for item in self.overdue), ZERO)

    @property
    def overdue_total(self):
        return self.overdue_interest + self.overdue_principal

    def drop_settled_overdue(self):
        self.overdue = [item for item in self.overdue if item.interest > 0 or item.principal > 0]


def get_debt_state(debt, as_of=None, payments=None):
    """Return the debt state at the start of `as_of` (today by default)."""
    as_of = as_of or date.today()
    if payments is None:
        payments = _load_payments(debt)
    return _AccrualEngine(debt, payments).run(as_of)


def opening_principal(debt, payments):
    """Principal on the accrual start date implied by the stored balances."""
    start = accrual_start_date(debt)
    later = [payment for payment in payments if start is None or payment.payment_date > start]
    return money(debt.remaining_amount) + sum((_stored_principal(payment) for payment in later), ZERO)


def accrual_start_date(debt):
    if getattr(debt, 'accrual_start_date', None):
        return debt.accrual_start_date
    if getattr(debt, 'interest_period_start_date', None):
        return debt.interest_period_start_date
    if getattr(debt, 'next_payment_date', None):
        return previous_payment_due_date(debt, debt.next_payment_date)
    created_at = getattr(debt, 'created_at', None)
    return created_at.date() if created_at else None


def _load_payments(debt):
    # Only stored debts read payments from the database: demo debts carry
    # made-up ids that may belong to someone else's rows.
    state = sa_inspect(debt, raiseerr=False)
    if not getattr(debt, 'id', None) or state is None or not state.persistent:
        return list(getattr(debt, 'payments', None) or [])
    from app.models import Payment
    return Payment.query.filter_by(debt_id=debt.id).all()


def _stored_principal(payment):
    value = payment.principal_amount if payment.principal_amount is not None else payment.amount
    return money(value)


def _is_manual(payment):
    return getattr(payment, 'breakdown_source', 'auto') == 'manual'


def _sort_key(payment):
    return (payment.payment_date, payment.id or 0)


class _AccrualEngine:
    def __init__(self, debt, payments):
        self.debt = debt
        self.start = accrual_start_date(debt)
        self.all_payments = sorted(
            (payment for payment in payments if payment.payment_date),
            key=_sort_key,
        )
        self.payments = [
            payment for payment in self.all_payments
            if self.start is None or payment.payment_date > self.start
        ]
        self.penalty_rate = Decimal(str(getattr(debt, 'penalty_rate', None) or 0))
        self.capitalize = bool(getattr(debt, 'capitalize_interest', False))
        self.include_payment_day = bool(getattr(debt, 'include_payment_day', False))
        # Accrual ranges never overlap, so the payment day is handled here by
        # shifting the range instead of extending its end.
        self.rate_source = SimpleNamespace(
            interest_rate_for=debt.interest_rate_for,
            interest_rate_change_date=getattr(debt, 'interest_rate_change_date', None),
            day_count_convention=getattr(debt, 'day_count_convention', 'actual_year'),
            include_payment_day=False,
        )
        self.grace_start, self.grace_end = self._grace_window()
        self.periods = []
        self.cycles = []
        self.allocations = {}

    # --- configuration ---------------------------------------------------
    @property
    def debt_type(self):
        return getattr(self.debt, 'debt_type', None)

    def _grace_window(self):
        if self.debt_type != 'credit_card':
            return None, None
        end = getattr(self.debt, 'grace_period_end_date', None)
        if not end:
            return None, None
        start = getattr(self.debt, 'interest_period_start_date', None) or self.start
        return start, end

    def _accrues_interest(self):
        if self.debt_type == 'split':
            return False
        if self.debt_type == 'credit_card' and self.grace_end is None:
            return False
        return True

    def _is_cyclic(self):
        return self.debt_type == 'split' or bool(getattr(self.debt, 'is_payment_recurring', False))

    # --- calendar --------------------------------------------------------
    def _due_dates(self, after, limit_date):
        """Due dates d with after < d, generated until d >= limit_date."""
        anchor = getattr(self.debt, 'next_payment_date', None)
        if not anchor:
            return
        if not self._is_cyclic():
            if after is None or anchor > after:
                yield anchor
            return
        if self.debt_type == 'split':
            due = anchor
            for _ in range(MAX_CYCLES):
                previous = previous_payment_due_date(self.debt, due)
                if after is None or previous <= after:
                    break
                due = previous
            while after is not None and due <= after:
                due = next_payment_due_date(self.debt, due)
            for _ in range(MAX_CYCLES):
                yield due
                if due >= limit_date:
                    return
                due = next_payment_due_date(self.debt, due)
            return
        # Monthly dates are offsets from one anchor so the day of month
        # does not drift after short months.
        offset = 0
        if after is not None:
            while anchor + relativedelta(months=offset - 1) > after and offset > -MAX_CYCLES:
                offset -= 1
            while anchor + relativedelta(months=offset) <= after and offset < MAX_CYCLES:
                offset += 1
        for _ in range(MAX_CYCLES):
            due = anchor + relativedelta(months=offset)
            yield due
            if due >= limit_date:
                return
            offset += 1

    def _previous_due(self, due):
        if self.debt_type == 'split' or not self._is_cyclic():
            return previous_payment_due_date(self.debt, due)
        anchor = self.debt.next_payment_date
        months = (due.year - anchor.year) * 12 + due.month - anchor.month
        return anchor + relativedelta(months=months - 1)

    # --- accrual ---------------------------------------------------------
    def _accrue(self, ledger, until):
        if until <= ledger.cursor:
            return
        start, end = ledger.cursor, until
        if self._accrues_interest() and ledger.principal > 0:
            if self.include_payment_day:
                start, end = start + timedelta(days=1), end + timedelta(days=1)
            in_grace = self.grace_end is not None and until <= self.grace_end + timedelta(days=1)
            for segment in calculate_interest_segments(self.rate_source, ledger.principal, start, end):
                if in_grace:
                    ledger.grace_interest += segment['interest']
                else:
                    ledger.current_interest += segment['interest']
                self._record_period('grace_interest' if in_grace else 'interest', segment, ledger.principal)
        if self.penalty_rate > 0 and ledger.overdue_total > 0:
            base = ledger.overdue_total
            day = ledger.cursor
            while day < until:
                segment_end = min(date(day.year + 1, 1, 1), until)
                days = (segment_end - day).days
                year_days = days_in_year(self.debt, day.year)
                amount = base * self.penalty_rate / Decimal('100') * Decimal(days) / Decimal(year_days)
                ledger.penalty += amount
                self._record_period('penalty', {
                    'start': day,
                    'end': segment_end,
                    'days': days,
                    'rate': self.penalty_rate,
                    'days_in_year': year_days,
                    'interest': amount,
                }, base)
                day = segment_end
        ledger.cursor = until

    def _record_period(self, kind, segment, base):
        self.periods.append({
            'kind': kind,
            'start': segment['start'],
            'end': segment['end'],
            'days': segment['days'],
            'rate': segment['rate'],
            'days_in_year': segment['days_in_year'],
            'base': money(base),
            'amount': money(segment['interest']),
        })

    def _settle_grace(self, ledger):
        # Interest for the whole grace period becomes payable only if the
        # grace balance was not repaid by the end of the period.
        if ledger.principal > 0:
            ledger.current_interest += ledger.grace_interest
            for period in self.periods:
                if period['kind'] == 'grace_interest':
                    period['kind'] = 'interest'
        else:
            self.periods = [period for period in self.periods if period['kind'] != 'grace_interest']
        ledger.grace_interest = Decimal('0')

    # --- cycles ----------------------------------------------------------
    def _required_payment(self, cycle_index, cycle_opening_principal):
        first_payment = money(getattr(self.debt, 'first_payment_amount', None))
        if cycle_index == 0 and first_payment > 0 and not self._required_payment_before_start():
            return first_payment
        minimum_payment = money(getattr(self.debt, 'minimum_payment', None))
        if minimum_payment > 0:
            return minimum_payment
        if self.debt_type == 'split':
            return split_default_payment_amount(cycle_opening_principal)
        if not self._is_cyclic():
            return None
        return ZERO

    def _required_payment_before_start(self):
        return any(
            not getattr(payment, 'is_early_repayment', False)
            or money(getattr(payment, 'scheduled_payment_amount', None)) > 0
            for payment in self.all_payments
            if self.start is not None and payment.payment_date <= self.start
        )

    def _paid_in_cycle(self, cycle_start, due, through=None):
        through = through or due
        return money(sum(
            (self._cycle_credit(payment) for payment in self.payments
             if cycle_start < payment.payment_date <= through),
            ZERO,
        ))

    def _cycle_credit(self, payment):
        # Money that settled an earlier overdue or the penalty does not pay
        # the current cycle.
        credit = scheduled_payment_component(payment)
        allocation = self.allocations.get(payment.id)
        if allocation:
            credit -= allocation['overdue_interest'] + allocation['overdue_principal'] + allocation['penalty']
        return max(credit, ZERO)

    def _close_cycle(self, ledger, due, cycle_index, cycle_opening_principal):
        interest_due = money(ledger.current_interest)
        ledger.current_interest = Decimal('0')
        if self.capitalize and interest_due > 0:
            ledger.principal += interest_due
            ledger.capitalized_interest += interest_due
            interest_due = ZERO
        cycle_start = self._previous_due(due)
        paid = self._paid_in_cycle(cycle_start, due)
        not_overdue_principal = ledger.principal - ledger.overdue_principal
        required = self._required_payment(cycle_index, cycle_opening_principal)
        if required is None:
            required = not_overdue_principal + interest_due + paid
        shortfall = min(max(required - paid, ZERO), not_overdue_principal + interest_due)
        overdue_interest = min(interest_due, shortfall)
        overdue_principal = min(shortfall - overdue_interest, not_overdue_principal)
        ledger.current_interest = interest_due - overdue_interest
        if overdue_interest > 0 or overdue_principal > 0:
            ledger.overdue.append(_OverdueItem(due, overdue_interest, overdue_principal))
        self.cycles.append({
            'due_date': due,
            'required': money(required),
            'paid': paid,
            'shortfall': money(shortfall),
            'interest_due': interest_due,
            'on_time': shortfall <= 0,
        })

    # --- payments --------------------------------------------------------
    def _apply_payment(self, ledger, payment):
        fee = money(payment.fee_amount)
        if _is_manual(payment):
            allocation = self._apply_manual(ledger, payment, fee)
        else:
            allocation = self._apply_auto(ledger, payment, fee)
        allocation['remaining_after'] = money(ledger.principal)
        self.allocations[payment.id] = allocation

    def _apply_auto(self, ledger, payment, fee):
        available = money(payment.amount) - fee
        if getattr(payment, 'is_early_repayment', False):
            scheduled = min(money(getattr(payment, 'scheduled_payment_amount', None)), available)
        else:
            scheduled = available
        left = available
        paid_overdue_interest, left = self._pay_overdue(ledger, 'interest', left)
        paid_overdue_principal, left = self._pay_overdue(ledger, 'principal', left)
        ledger.principal -= paid_overdue_principal
        penalty = money(ledger.penalty)
        paid_penalty = min(left, penalty)
        ledger.penalty = penalty - paid_penalty
        left -= paid_penalty
        scheduled_left = max(scheduled - (available - left), ZERO)
        current_interest = money(ledger.current_interest)
        paid_current_interest = min(left, scheduled_left, current_interest)
        ledger.current_interest = current_interest - paid_current_interest
        left -= paid_current_interest
        paid_principal = min(left, ledger.principal)
        ledger.principal -= paid_principal
        left -= paid_principal
        return {
            'principal': paid_overdue_principal + paid_principal,
            'interest': paid_overdue_interest + paid_current_interest,
            'penalty': paid_penalty,
            'fee': fee,
            'overdue_interest': paid_overdue_interest,
            'overdue_principal': paid_overdue_principal,
            'current_interest': paid_current_interest,
            'excess': left,
            'source': 'auto',
        }

    def _apply_manual(self, ledger, payment, fee):
        interest = money(payment.interest_amount)
        principal = min(_stored_principal(payment), ledger.principal)
        penalty = money(getattr(payment, 'penalty_amount', None))
        paid_overdue_interest, interest_left = self._pay_overdue(ledger, 'interest', interest)
        current_interest = money(ledger.current_interest)
        ledger.current_interest = max(current_interest - interest_left, ZERO)
        paid_overdue_principal, _ = self._pay_overdue(ledger, 'principal', principal)
        ledger.principal -= principal
        ledger.penalty = max(money(ledger.penalty) - penalty, ZERO)
        return {
            'principal': principal,
            'interest': interest,
            'penalty': penalty,
            'fee': fee,
            'overdue_interest': paid_overdue_interest,
            'overdue_principal': paid_overdue_principal,
            'current_interest': min(interest_left, current_interest),
            'excess': ZERO,
            'source': 'manual',
        }

    @staticmethod
    def _pay_overdue(ledger, part, amount):
        paid = ZERO
        for item in ledger.overdue:
            if amount <= 0:
                break
            owed = getattr(item, part)
            take = min(owed, amount)
            setattr(item, part, owed - take)
            paid += take
            amount -= take
        ledger.drop_settled_overdue()
        return paid, amount

    # --- driver ----------------------------------------------------------
    def run(self, as_of):
        opening = opening_principal(self.debt, self.all_payments)
        start = self.start or as_of
        ledger = _Ledger(principal=opening, cursor=min(start, as_of))
        if self.grace_start is not None and self.grace_start < start and self._accrues_interest():
            # The grace period began before accruals are tracked; the balance
            # on the start date stands in for the earlier days.
            for segment in calculate_interest_segments(self.rate_source, opening, self.grace_start, start):
                ledger.grace_interest += segment['interest']
                self._record_period('grace_interest', segment, opening)

        events = [(payment.payment_date, 1, payment) for payment in self.payments if payment.payment_date <= as_of]
        for index, due in enumerate(self._due_dates(self.start, as_of)):
            if due >= as_of:
                break
            events.append((due, 2, index))
        if self.grace_end is not None:
            settle_date = self.grace_end + timedelta(days=1)
            if start < settle_date <= as_of:
                events.append((settle_date, 0, None))
        # Stable sort keeps payments of one day in (date, id) order; on a due
        # date the payments come before the cycle is closed.
        events.sort(key=lambda event: (event[0], event[1]))

        cycle_opening_principal = ledger.principal
        for event_date, kind, payload in events:
            self._accrue(ledger, max(event_date, ledger.cursor))
            if kind == 0:
                self._settle_grace(ledger)
            elif kind == 1:
                self._apply_payment(ledger, payload)
            else:
                self._close_cycle(ledger, event_date, payload, cycle_opening_principal)
                cycle_opening_principal = ledger.principal
        self._accrue(ledger, as_of)
        return self._result(ledger, as_of, opening)

    def _next_due(self, ledger, as_of):
        if ledger.overdue:
            item = ledger.overdue[0]
            return item.due_date, money(item.interest + item.principal)
        cycle_index = len(self.cycles)
        for due in self._due_dates(self.start, date.max):
            if due < as_of:
                continue
            required = self._required_payment(cycle_index, ledger.principal)
            cycle_index += 1
            if required is None:
                return due, money(ledger.principal + ledger.current_interest)
            paid = self._paid_in_cycle(self._previous_due(due), due, through=as_of)
            if required <= 0 or paid < required or not self._is_cyclic():
                return due, money(max(required - paid, ZERO))
            if cycle_index > MAX_CYCLES:
                break
        return getattr(self.debt, 'next_payment_date', None), ZERO

    def _result(self, ledger, as_of, opening):
        principal = money(ledger.principal)
        current_interest = money(ledger.current_interest)
        overdue_interest = money(ledger.overdue_interest)
        overdue_principal = money(ledger.overdue_principal)
        penalty = money(ledger.penalty)
        accrued_interest = current_interest + overdue_interest
        overdue_since = ledger.overdue[0].due_date if ledger.overdue else None
        next_due_date, next_due_amount = self._next_due(ledger, as_of) if principal > 0 or ledger.overdue else (None, ZERO)
        return {
            'as_of': as_of,
            'accrual_start_date': self.start,
            'opening_principal': money(opening),
            'principal': principal,
            # remaining_amount stores principal without capitalized interest,
            # so opening_principal stays the same across recalculations.
            'capitalized_interest': money(ledger.capitalized_interest),
            'current_interest': current_interest,
            'overdue_interest': overdue_interest,
            'overdue_principal': overdue_principal,
            'accrued_interest': accrued_interest,
            'penalty': penalty,
            'total': principal + accrued_interest + penalty,
            'overdue_amount': overdue_interest + overdue_principal,
            'amount_to_clear_overdue': overdue_interest + overdue_principal + penalty,
            'is_overdue': overdue_since is not None,
            'overdue_since': overdue_since,
            'overdue_items': [
                {
                    'due_date': item.due_date,
                    'interest': money(item.interest),
                    'principal': money(item.principal),
                    'amount': money(item.interest + item.principal),
                    'days': (as_of - item.due_date).days,
                }
                for item in ledger.overdue
            ],
            'overdue_days': (as_of - overdue_since).days if overdue_since else 0,
            'accrues_interest': self._accrues_interest(),
            'grace_interest_pending': money(ledger.grace_interest),
            'grace_period_end_date': self.grace_end,
            'penalty_rate': self.penalty_rate if self.penalty_rate > 0 else None,
            'next_due_date': next_due_date,
            'next_due_amount': next_due_amount,
            'periods': self.periods,
            'cycles': self.cycles,
            'allocations': self.allocations,
        }
