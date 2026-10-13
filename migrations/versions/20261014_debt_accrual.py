"""Add penalty, capitalization, grace period and accrual anchor fields.

Debts get the settings the accrual calculation needs; payments record the
penalty part and whether the breakdown was typed in by the user. Existing
debts keep an empty penalty rate (only new debts get the default) and start
tracking accruals from their current payment cycle.

Revision ID: 20261014_debt_accrual
Revises: 20261013_debt_enums
Create Date: 2026-10-14 09:00:00.000000
"""

from calendar import monthrange
from datetime import date

from alembic import op
import sqlalchemy as sa
from dateutil.relativedelta import relativedelta


revision = '20261014_debt_accrual'
down_revision = '20261013_debt_enums'
branch_labels = None
depends_on = None

DEBT_COLUMNS = (
    ('penalty_rate', sa.Numeric(5, 2), True, None),
    ('capitalize_interest', sa.Boolean(), False, sa.false()),
    ('grace_period_end_date', sa.Date(), True, None),
    ('accrual_start_date', sa.Date(), True, None),
)
PAYMENT_COLUMNS = (
    ('penalty_amount', sa.Numeric(12, 2), False, '0'),
    ('breakdown_source', sa.Enum('auto', 'manual'), False, 'auto'),
)


def _column_exists(table_name, column_name):
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column['name'] for column in inspector.get_columns(table_name)}


def _add_columns(table_name, columns):
    for name, column_type, nullable, default in columns:
        if _column_exists(table_name, name):
            continue
        kwargs = {'nullable': nullable}
        if default is not None:
            kwargs['server_default'] = default
        op.add_column(table_name, sa.Column(name, column_type, **kwargs))


def _as_date(value):
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _previous_split_cycle_date(current_date):
    anchor_day = current_date.day
    second_day = anchor_day + 15 if anchor_day <= 15 else anchor_day - 15
    payment_days = sorted({max(min(anchor_day, 31), 1), max(min(second_day, 31), 1)}, reverse=True)
    for month_offset in range(0, -24, -1):
        month_start = date(current_date.year, current_date.month, 1) + relativedelta(months=month_offset)
        last_day = monthrange(month_start.year, month_start.month)[1]
        for day in payment_days:
            candidate = date(month_start.year, month_start.month, min(day, last_day))
            if candidate < current_date:
                return candidate
    return current_date - relativedelta(days=14)


def _current_cycle_start(debt_type, next_payment_date, interest_period_start_date):
    if debt_type == 'split':
        start = _previous_split_cycle_date(next_payment_date)
    else:
        start = next_payment_date - relativedelta(months=1)
    if interest_period_start_date and start < interest_period_start_date < next_payment_date:
        return interest_period_start_date
    return start


def _backfill():
    bind = op.get_bind()
    debts = bind.execute(sa.text(
        'SELECT id, debt_type, next_payment_date, interest_period_start_date FROM debts '
        'WHERE next_payment_date IS NOT NULL AND accrual_start_date IS NULL'
    )).fetchall()
    for debt_id, debt_type, next_payment_date, interest_period_start_date in debts:
        start = _current_cycle_start(
            debt_type,
            _as_date(next_payment_date),
            _as_date(interest_period_start_date),
        )
        bind.execute(
            sa.text('UPDATE debts SET accrual_start_date = :start WHERE id = :id'),
            {'start': start, 'id': debt_id},
        )
    # The old automatic split sent on-time payments fully to principal, so a
    # stored interest or fee part was typed in (or estimated for a late
    # payment) and is kept as entered.
    bind.execute(sa.text(
        "UPDATE payments SET breakdown_source = 'manual' "
        'WHERE interest_amount > 0 OR fee_amount > 0'
    ))


def upgrade():
    _add_columns('debts', DEBT_COLUMNS)
    _add_columns('payments', PAYMENT_COLUMNS)
    _backfill()

    if op.get_bind().dialect.name not in ('sqlite',):
        op.alter_column('debts', 'capitalize_interest', server_default=None)
        op.alter_column('payments', 'penalty_amount', server_default=None)
        op.alter_column('payments', 'breakdown_source', server_default=None)


def downgrade():
    for name, *_ in reversed(PAYMENT_COLUMNS):
        if _column_exists('payments', name):
            op.drop_column('payments', name)
    for name, *_ in reversed(DEBT_COLUMNS):
        if _column_exists('debts', name):
            op.drop_column('debts', name)
