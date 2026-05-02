import re
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from flask import request, redirect, url_for, render_template, jsonify, flash
from flask_login import current_user
from extensions import db
from app.models import AppSetting, DictionaryEntry, ActivityLog

DICTIONARY_TYPES = [
    ('bank', 'Банки'),
    ('debt_type', 'Типы долгов'),
    ('debt_category', 'Категории долгов'),
    ('status', 'Статусы'),
    ('comment_template', 'Шаблоны комментариев'),
    ('interest_rate', 'Стандартные процентные ставки'),
    ('product_type', 'Типы финансовых продуктов'),
]

INCOME_CATEGORIES = [
    ('salary', 'Зарплата'),
    ('advance', 'Аванс'),
    ('side_job', 'Подработка'),
    ('debt_return', 'Возврат долга'),
    ('bonus', 'Премия'),
    ('scholarship', 'Стипендия'),
    ('other', 'Другое'),
]

EXPENSE_CATEGORIES = [
    ('products', 'Продукты'),
    ('transport', 'Транспорт'),
    ('communication', 'Связь'),
    ('rent', 'Аренда'),
    ('loans', 'Кредиты'),
    ('entertainment', 'Развлечения'),
    ('health', 'Здоровье'),
    ('education', 'Обучение'),
    ('clothing', 'Одежда'),
    ('subscriptions', 'Подписки'),
    ('other', 'Другое'),
]

PAYMENT_METHODS = [
    ('card', 'Карта'),
    ('cash', 'Наличные'),
    ('transfer', 'Перевод'),
    ('other', 'Другое'),
]

DEFAULT_SETTINGS = {
    'app_name': 'ДолгТрекер',
    'default_currency': 'RUB',
    'registration_enabled': 'false',
    'telegram_login_enabled': 'true',
    'debt_limit_per_user': '50',
    'archive_enabled': 'true',
    'export_enabled': 'true',
    'payment_warning_days': '7',
    'urgent_payment_days': '3',
    'overdue_after_date': 'true',
}


def format_currency(value):
    if value is None or (isinstance(value, str) and str(value).strip() == ''):
        return '—'
    try:
        amount = Decimal(str(value))
    except Exception:
        return str(value)

    if amount == amount.to_integral():
        formatted = '{:,.0f}'.format(amount)
        return formatted.replace(',', ' ') + ' ₽'

    amount = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    formatted = '{:,.2f}'.format(amount).replace(',', ' ').replace('.', ',')
    return formatted + ' ₽'


def parse_decimal(value, field_name, required=True):
    if value is None or str(value).strip() == '':
        if required:
            raise ValueError(f"Поле '{field_name}' обязательно для заполнения")
        return None
    try:
        normalized = re.sub(r'\s+', '', str(value)).replace(',', '.')
        result = Decimal(normalized)
        if result < 0:
            raise ValueError(f"Поле '{field_name}' не может быть отрицательным")
        return result
    except InvalidOperation:
        raise ValueError(f"Поле '{field_name}' содержит некорректное число")


def parse_date(value, field_name, required=False):
    if not value or str(value).strip() == '':
        if required:
            raise ValueError(f"Поле '{field_name}' обязательно для заполнения")
        return None
    try:
        return datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
    except ValueError:
        raise ValueError(f"Поле '{field_name}' содержит некорректную дату (формат: ГГГГ-ММ-ДД)")


def get_setting(key, default=None):
    try:
        setting = AppSetting.query.filter_by(key=key).first()
        return setting.value if setting else default
    except Exception:
        return default


def get_dictionary_values(dictionary_type):
    try:
        entries = DictionaryEntry.query.filter_by(dictionary_type=dictionary_type, is_active=True).order_by(DictionaryEntry.value.asc()).all()
        return [entry.value for entry in entries]
    except Exception:
        return []


def is_local_test_user(user=None):
    if user is None:
        user = current_user
    return getattr(user, 'is_local_test_user', False) or getattr(user, 'id', None) == 'test-user'


def group_entries_by_month(entries, date_attr):
    grouped = OrderedDict()
    for entry in entries:
        entry_date = getattr(entry, date_attr, None)
        if not entry_date:
            continue
        key = entry_date.strftime('%Y-%m')
        if key not in grouped:
            grouped[key] = {
                'year_month': key,
                'title': entry_date.strftime('%m.%Y'),
                'date': entry_date,
                'items': [],
            }
        grouped[key]['items'].append(entry)

    for group in grouped.values():
        group['items'].sort(key=lambda item: getattr(item, date_attr), reverse=True)

    return sorted(grouped.values(), key=lambda group: group['date'], reverse=True)


def set_setting(key, value, description=None):
    setting = AppSetting.query.filter_by(key=key).first()
    if not setting:
        setting = AppSetting(key=key, value=value, description=description)
        db.session.add(setting)
    else:
        setting.value = value
        if description is not None:
            setting.description = description
    db.session.commit()
    return setting


def record_activity(action, user=None, entity_type=None, entity_id=None, description=None, ip_address=None, user_agent=None):
    user_id = getattr(user, 'id', None) if user else None
    if not isinstance(user_id, int):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            user_id = None

    log = ActivityLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.session.add(log)
    db.session.commit()


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not getattr(current_user, 'is_authenticated', False) or not getattr(current_user, 'is_admin', False):
            return redirect(url_for('index'))
        return view(*args, **kwargs)
    return wrapper


def superadmin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not getattr(current_user, 'is_authenticated', False) or not getattr(current_user, 'is_superadmin', False):
            flash('Недостаточно прав для доступа к этому разделу.', 'warning')
            if getattr(current_user, 'is_admin', False):
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapper
