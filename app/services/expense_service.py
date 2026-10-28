"""Validated expense writes shared by the web UI and Telegram bot.

The default transaction boundary and monthly generation match the web UI.
With commit=False the caller owns commit/rollback (for example, the bot also
clears its conversation). Monthly writes require the default boundary because
the existing generator commits independently. Extra keyword fields are reserved
for future integrations and rejected until explicitly supported.
"""

import uuid

from app.models import Expense, User, Vehicle
from app.services.monthly_expenses_service import (
    find_monthly_expense_for_month,
    generate_monthly_expenses_from_start_date,
)
from app.time_utils import utc_now
from app.utils import EXPENSE_CATEGORIES, PAYMENT_METHODS, parse_date, parse_decimal, record_activity
from extensions import db

_UNCHANGED = object()


def validate_vehicle_amount_change(expense, amount):
    """Shared guard for the import's existing monthly-expense update path."""
    if expense.vehicle_id:
        _check_vehicle_update(expense, expense.vehicle_id, amount)


def _vehicle(user_id, vehicle_id):
    from app.services.vehicle_service import get_vehicle_for_user
    return get_vehicle_for_user(db.session.get(User, user_id), vehicle_id, for_update=True)


def _existing_vehicle(user_id, vehicle_id):
    """Existing financial records remain usable when the garage UI is disabled."""
    from flask import abort
    from app.services.personal_access_service import can_use_personal_cabinet
    user = db.session.get(User, user_id)
    if not can_use_personal_cabinet(user) or getattr(user, 'is_local_test_user', False):
        abort(404)
    return Vehicle.query.filter_by(user_id=user_id, id=vehicle_id).with_for_update().first_or_404()


def _check_vehicle_update(expense, vehicle_id, amount):
    if expense.vehicle_id:
        vehicle = _existing_vehicle(expense.user_id, expense.vehicle_id)
        if vehicle.archived_at and (vehicle_id != expense.vehicle_id or amount != expense.amount):
            raise ValueError('Автомобиль в архиве: нельзя менять автомобиль и сумму расхода до восстановления.')
    if vehicle_id and vehicle_id != expense.vehicle_id:
        vehicle = _vehicle(expense.user_id, vehicle_id)
        if vehicle.archived_at or vehicle.status != 'active':
            raise ValueError('Выберите активный автомобиль вне архива.')


def _validate(*, amount, category, title, expense_date, payment_method, comment):
    amount = parse_decimal(amount, 'Сумма', required=True)
    if amount <= 0:
        raise ValueError('Сумма расхода должна быть больше нуля')
    if category not in [item[0] for item in EXPENSE_CATEGORIES]:
        raise ValueError('Выберите корректную категорию расхода')
    title = str(title).strip()
    if not title:
        raise ValueError('Название расхода обязательно')
    if len(title) > 150:
        raise ValueError('Название расхода не должно превышать 150 символов')
    expense_date = parse_date(expense_date, 'Дата', required=True)
    payment_method = str(payment_method or '').strip() or None
    comment = str(comment or '').strip() or None
    if payment_method and payment_method not in [item[0] for item in PAYMENT_METHODS]:
        raise ValueError('Выберите корректный способ оплаты')
    if comment and len(comment) > 2000:
        raise ValueError('Комментарий не должен превышать 2000 символов')
    return dict(amount=amount, category=category, title=title, expense_date=expense_date,
                payment_method=payment_method, comment=comment)


def _check_options(extra_fields):
    if extra_fields:
        raise TypeError('Unsupported expense options: ' + ', '.join(sorted(extra_fields)))


def _owned_expense(user_id, expense_id):
    return Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()


def _monthly_settings(expense):
    if not expense.monthly_group_id:
        expense.monthly_group_id = str(uuid.uuid4())
    expense.generated_for_month = expense.expense_date.strftime('%Y-%m')
    expense.monthly_anchor_day = expense.expense_date.day
    expense.monthly_settings_updated_at = utc_now()


def _save(expense, action, commit):
    db.session.flush()
    record_activity(action, db.session.get(User, expense.user_id), entity_type='expense',
                    entity_id=expense.id, description=expense.category, commit=False)
    if commit:
        db.session.commit()
        if expense.is_monthly:
            generate_monthly_expenses_from_start_date(expense.id)
    return expense


def create_expense(*, user_id, amount, title, expense_date, category=None,
                   payment_method=None, comment=None, is_monthly=False, commit=True, vehicle_id=None, **extra_fields):
    _check_options(extra_fields)
    if vehicle_id:
        vehicle = _vehicle(user_id, vehicle_id)
        if vehicle.archived_at or vehicle.status != 'active':
            raise ValueError('Нельзя добавить расход: выберите активный автомобиль вне архива.')
        category = category or 'transport'
    values = _validate(amount=amount, category=category, title=title, expense_date=expense_date,
                       payment_method=payment_method, comment=comment)
    if is_monthly and not commit:
        raise ValueError('Monthly expenses require commit=True')
    expense = Expense(user_id=user_id, is_monthly=is_monthly, vehicle_id=vehicle_id or None, **values)
    if is_monthly:
        _monthly_settings(expense)
    db.session.add(expense)
    return _save(expense, 'expense_create', commit)


def update_expense(*, user_id, expense_id, amount, category, title, expense_date,
                   payment_method=None, comment=None, is_monthly=False, commit=True, vehicle_id=_UNCHANGED, **extra_fields):
    _check_options(extra_fields)
    expense = _owned_expense(user_id, expense_id)
    values = _validate(amount=amount, category=category, title=title, expense_date=expense_date,
                       payment_method=payment_method, comment=comment)
    vehicle_id = expense.vehicle_id if vehicle_id is _UNCHANGED else (vehicle_id or None)
    _check_vehicle_update(expense, vehicle_id, values['amount'])
    if is_monthly and not commit:
        raise ValueError('Monthly expenses require commit=True')
    was_monthly = expense.is_monthly
    monthly_group_id = expense.monthly_group_id
    # Check before mutating so a duplicate never autoflushes an invalid occurrence.
    group_id = (monthly_group_id or str(uuid.uuid4())) if is_monthly else monthly_group_id
    if is_monthly and find_monthly_expense_for_month(
        user_id, group_id, values['expense_date'].strftime('%Y-%m'), exclude_expense_id=expense.id,
    ):
        raise ValueError('Для этого ежемесячного расхода уже есть запись в выбранном месяце.')
    expense.vehicle_id = vehicle_id
    for key, value in values.items():
        setattr(expense, key, value)
    if is_monthly:
        expense.is_monthly = True
        expense.monthly_group_id = group_id
        _monthly_settings(expense)
    elif was_monthly and monthly_group_id:
        for group_expense in Expense.query.filter_by(user_id=user_id, monthly_group_id=monthly_group_id).all():
            group_expense.is_monthly = False
    else:
        expense.is_monthly = False
    return _save(expense, 'expense_update', commit)


def delete_expense(*, user_id, expense_id, commit=True, **extra_fields):
    _check_options(extra_fields)
    expense = _owned_expense(user_id, expense_id)
    if expense.vehicle_id and _existing_vehicle(user_id, expense.vehicle_id).archived_at:
        raise ValueError('Автомобиль в архиве: сначала восстановите его, чтобы удалить расход.')
    Expense.query.filter_by(user_id=user_id, generated_from_id=expense.id).update(
        {Expense.generated_from_id: None}, synchronize_session=False,
    )
    record_activity('expense_delete', db.session.get(User, user_id), entity_type='expense',
                    entity_id=expense.id, description=expense.category, commit=False)
    db.session.delete(expense)
    if commit:
        db.session.commit()
