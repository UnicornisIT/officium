"""Напоминания о списаниях по активным ежемесячным сериям."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Expense, MonthlyExpenseReminderLog, User, Vehicle
from app.services import telegram_bot_service as telegram
from app.services.monthly_expenses_service import (
    _date_in_month, _pick_origin_expense, _pick_settings_expense,
)
from app.services.telegram_transport import TelegramDeliveryError
from app.services.telegram_links_service import application_url, navigation_keyboard
from app.time_utils import as_utc, utc_now
from app.utils import format_currency
from extensions import db


def _due_expenses(session, user, now):
    local = now.astimezone(ZoneInfo(user.timezone))
    clock = time.fromisoformat(user.reminder_time)
    scheduled = datetime.combine(local.date(), clock, tzinfo=local.tzinfo)
    elapsed = now - scheduled.astimezone(now.tzinfo)
    if not timedelta(0) <= elapsed <= timedelta(hours=6):
        return []
    target = local.date() + timedelta(days=user.reminder_days_before)
    rows = session.scalars(select(Expense).where(Expense.user_id == user.id)).all()
    groups = {}
    for expense in rows:
        if expense.expense_date is None:
            continue
        groups.setdefault(expense.monthly_group_id or f'legacy:{expense.id}', []).append(expense)
    sent_ids = set(session.scalars(select(MonthlyExpenseReminderLog.monthly_expense_id).where(
        MonthlyExpenseReminderLog.user_id == user.id,
        MonthlyExpenseReminderLog.due_date == target,
    )))
    archived = set(session.scalars(select(Vehicle.id).where(Vehicle.user_id == user.id,
                                                            Vehicle.archived_at.isnot(None))))
    due = []
    for group in groups.values():
        active = [expense for expense in group if expense.is_monthly]
        if not active:
            continue
        origin = _pick_origin_expense(group)
        settings = _pick_settings_expense(active)
        if (origin.id in sent_ids or settings.amount is None or settings.expense_date is None
                or settings.vehicle_id in archived or origin.expense_date is None
                or origin.expense_date > target):
            continue
        anchor = settings.monthly_anchor_day or origin.monthly_anchor_day or origin.expense_date.day
        if not 1 <= anchor <= 31 or _date_in_month(anchor, target) != target:
            continue
        # Снимок конкретного месяца может отличаться от будущих настроек серии.
        occurrence = next((row for row in group if row.expense_date == target), settings)
        if occurrence.amount is not None:
            due.append((origin.id, occurrence.title, Decimal(occurrence.amount), target))
    return sorted(due, key=lambda item: (item[1].casefold(), item[0]))


def _message(due):
    lines = [f'Ежемесячные расходы на {due[0][3].strftime("%d.%m.%Y")}:']
    lines.extend(f'• {title}: {format_currency(amount)}' for _, title, amount, _ in due)
    lines.append(f'Итого: {format_currency(sum((row[2] for row in due), Decimal(0)))}')
    url = application_url('/expenses')
    if url:
        lines.append(f'Открыть расходы: {url}')
    return '\n'.join(lines)


def run(now_utc=None, dry_run=False) -> int:
    now = as_utc(now_utc or utc_now())
    with db.engine.connect() as connection:
        user_ids = connection.execute(select(User.id).where(
            User.reminder_enabled.is_(True), User.telegram_id > 0, User.is_blocked.is_(False),
        )).scalars().all()
    messages = 0
    for user_id in user_ids:
        with Session(db.engine) as session:
            try:
                # Блокировка до чтения журнала исключает параллельные отправки.
                if not dry_run and db.engine.dialect.name == 'sqlite':
                    session.execute(text('BEGIN IMMEDIATE'))
                query = select(User).where(User.id == user_id)
                if not dry_run:
                    query = query.with_for_update()
                user = session.scalar(query)
                if not user or not user.reminder_enabled or user.is_blocked or not user.telegram_id or user.telegram_id < 0:
                    continue
                due = _due_expenses(session, user, now)
                if not due:
                    continue
                if dry_run:
                    messages += 1
                    continue
                try:
                    delivered = telegram.send_telegram_message(
                        user.telegram_id,
                        _message(due),
                        reply_markup=navigation_keyboard(
                            'Открыть расходы', application_url('/expenses')
                        ),
                    )
                except TelegramDeliveryError as exc:
                    if exc.result.failure_kind in ('blocked', 'chat_unavailable'):
                        user.reminder_enabled = False
                        session.commit()
                        current_app.logger.warning('Monthly reminders disabled: user_id=%s kind=%s',
                                                   user_id, exc.result.failure_kind)
                    else:
                        current_app.logger.warning('Monthly reminder delivery failed: user_id=%s kind=%s',
                                                   user_id, exc.result.failure_kind)
                    continue
                if not delivered:
                    current_app.logger.warning('Monthly reminder delivery rejected: user_id=%s', user_id)
                    continue
                session.add_all(MonthlyExpenseReminderLog(
                    user_id=user_id, monthly_expense_id=expense_id, due_date=due_date,
                    sent_at=now.replace(tzinfo=None),
                ) for expense_id, _, _, due_date in due)
                session.commit()
                messages += 1
            except Exception:
                session.rollback()
                current_app.logger.warning('Monthly reminder processing failed: user_id=%s', user_id)
    return messages
