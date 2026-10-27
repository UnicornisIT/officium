import re
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from flask import request, redirect, url_for, render_template, jsonify
from flask_login import current_user
from extensions import db
from app.models import AppSetting, DictionaryEntry, ActivityLog

DICTIONARY_TYPES = [
    ('bank', 'Банки'),
    ('product_type', 'Названия продуктов'),
    ('interest_rate', 'Процентные ставки'),
    ('comment_template', 'Шаблоны комментариев'),
]
DICTIONARY_HELP = {
    'bank': 'Подсказки в поле «Банк» формы долга.',
    'product_type': 'Подсказки в поле «Продукт» формы долга.',
    'interest_rate': 'Подсказки в поле «Ставка, %» формы долга. Только числа от 0 до 200.',
    'comment_template': 'Кнопки быстрой вставки текста в комментарий к долгу.',
}
DICTIONARY_VALUE_MAX_LENGTH = 150

INCOME_CATEGORIES = [
    ('salary', 'Зарплата'),
    ('advance', 'Аванс'),
    ('side_job', 'Подработка'),
    ('debt_return', 'Возврат долга'),
    ('bonus', 'Премия'),
    ('scholarship', 'Стипендия'),
    ('vacation_pay', 'Отпускные'),
    ('other', 'Другое'),
]

# Categories that may still exist on old records but can no longer be chosen.
# 'goal_withdrawal' was set automatically for withdrawals from goals before
# goal operations became transfers; manually entered records keep it.
LEGACY_INCOME_CATEGORIES = [
    ('goal_withdrawal', 'Снятие с цели'),
]
INCOME_CATEGORY_LABELS = dict(INCOME_CATEGORIES + LEGACY_INCOME_CATEGORIES)


def income_category_choices(current=None):
    """Form choices; an old record keeps its legacy category selectable."""
    choices = list(INCOME_CATEGORIES)
    legacy = dict(LEGACY_INCOME_CATEGORIES)
    if current in legacy:
        choices.append((current, legacy[current]))
    return choices

EXPENSE_CATEGORIES = [
    ('products', 'Продукты'),
    ('transport', 'Транспорт'),
    ('communication', 'Связь'),
    ('rent', 'Аренда'),
    ('loans', 'Кредиты'),
    ('restaurants', 'Рестораны и кафе'),
    ('entertainment', 'Развлечения'),
    ('health', 'Здоровье'),
    ('education', 'Обучение'),
    ('clothing', 'Одежда'),
    ('subscriptions', 'Подписки'),
    ('savings', 'Накопления и цели'),
    ('other', 'Другое'),
]

PAYMENT_METHODS = [
    ('card', 'Карта'),
    ('cash', 'Наличные'),
    ('transfer', 'Перевод'),
    ('other', 'Другое'),
]

MONEY = Decimal('0.01')
MAX_MONEY = Decimal('9999999999.99')


def income_source_suggestions(incomes, limit=12):
    suggestions = []
    seen = set()
    for income in incomes:
        source = str(getattr(income, 'source', None) or '').strip()
        if not source:
            continue
        key = source.casefold()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(source)
        if len(suggestions) >= limit:
            break
    return suggestions

DEFAULT_SETTINGS = {
    'app_name': 'officium',
    'registration_enabled': 'false',
    'telegram_login_enabled': 'true',
    'debt_limit_per_user': '50',
    'archive_enabled': 'true',
    'export_enabled': 'true',
    'payment_warning_days': '7',
    'urgent_payment_days': '3',
    'impersonation_timeout_minutes': '30',
}


def format_currency(value):
    if value is None or (isinstance(value, str) and str(value).strip().lower() in ('', 'none', 'null')):
        return '—'
    try:
        amount = Decimal(str(value))
    except Exception:
        return str(value)

    if amount == amount.to_integral():
        formatted = '{:,.0f}'.format(amount)
        return formatted.replace(',', '\xa0') + '\xa0₽'

    amount = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    formatted = '{:,.2f}'.format(amount).replace(',', '\xa0').replace('.', ',')
    return formatted + '\xa0₽'


def display_value(value):
    if value is None:
        return '—'
    text = str(value).strip()
    if text.lower() in ('', 'none', 'null'):
        return '—'
    return text


def parse_decimal(value, field_name, required=True):
    if value is None or str(value).strip() == '':
        if required:
            raise ValueError(f"Поле '{field_name}' обязательно для заполнения")
        return None
    try:
        normalized = re.sub(r'\s+', '', str(value)).replace(',', '.')
        result = Decimal(normalized)
        if not result.is_finite():
            raise ValueError(f"Поле '{field_name}' содержит некорректное число")
        if result < 0:
            raise ValueError(f"Поле '{field_name}' не может быть отрицательным")
        if result > MAX_MONEY:
            raise ValueError(f"Поле '{field_name}' превышает максимально допустимое значение")
        return result.quantize(MONEY, rounding=ROUND_HALF_UP)
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


def value_is_enabled(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def telegram_feature_states(config):
    """Return configured, valid and effective Telegram states separately."""
    login_setting = value_is_enabled(
        get_setting('telegram_login_enabled', config.get('TELEGRAM_LOGIN_ENABLED', False))
    )
    token_ready = bool(str(config.get('TELEGRAM_BOT_TOKEN') or '').strip())
    username_ready = bool(str(config.get('TELEGRAM_BOT_USERNAME') or '').strip())
    login_valid = token_ready and username_ready
    login_effective = login_setting and login_valid
    mini_setting = value_is_enabled(config.get('TELEGRAM_MINI_APP_ENABLED', False))
    bot_setting = value_is_enabled(config.get('TELEGRAM_BOT_ENABLED', False))
    webhook_ready = bool(str(config.get('TELEGRAM_WEBHOOK_SECRET') or '').strip())
    return {
        'login': {
            'label': 'Вход через Telegram',
            'enabled_in_settings': login_setting,
            'configuration_valid': login_valid,
            'effective': login_effective,
            'verified': None,
        },
        'mini_app': {
            'label': 'Telegram Mini App',
            'enabled_in_settings': mini_setting,
            'configuration_valid': token_ready and username_ready,
            'effective': mini_setting and login_effective,
            'verified': None,
        },
        'bot': {
            'label': 'Telegram-бот',
            'enabled_in_settings': bot_setting,
            'configuration_valid': token_ready and webhook_ready,
            'effective': bot_setting and token_ready and webhook_ready,
            'verified': None,
        },
    }


def setting_enabled(key, default=True):
    value = get_setting(key, 'true' if default else 'false')
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _setting_int(key, default, minimum, maximum):
    try:
        value = int(str(get_setting(key, default)).strip())
    except (TypeError, ValueError):
        return default
    return min(max(value, minimum), maximum)


def payment_thresholds():
    """Days before a payment when it is highlighted as upcoming and as urgent."""
    warning = _setting_int('payment_warning_days', 7, 0, 365)
    urgent = min(_setting_int('urgent_payment_days', 3, 0, 365), warning)
    return {'warning': warning, 'urgent': urgent}


def normalize_dictionary_value(dictionary_type, raw_value):
    """Validate a dictionary value for its type; raises ValueError with a user message."""
    if dictionary_type not in dict(DICTIONARY_TYPES):
        raise ValueError('Выберите тип справочника.')
    value = ' '.join(str(raw_value or '').split())
    if not value:
        raise ValueError('Значение не может быть пустым.')
    if len(value) > DICTIONARY_VALUE_MAX_LENGTH:
        raise ValueError(f'Значение не должно превышать {DICTIONARY_VALUE_MAX_LENGTH} символов.')
    if dictionary_type == 'interest_rate':
        try:
            rate = Decimal(value.replace(',', '.').rstrip('%').strip())
        except InvalidOperation:
            raise ValueError('Ставка должна быть числом, например 18.5.')
        if not rate.is_finite() or not Decimal('0') <= rate <= Decimal('200'):
            raise ValueError('Ставка должна быть от 0 до 200.')
        value = format(rate.normalize(), 'f')
    return value


def get_dictionary_groups():
    """Active dictionary values grouped by type, loaded in one query."""
    groups = {key: [] for key, _ in DICTIONARY_TYPES}
    try:
        entries = DictionaryEntry.query.filter_by(is_active=True).order_by(DictionaryEntry.value.asc()).all()
    except Exception:
        return groups
    for entry in entries:
        groups.setdefault(entry.dictionary_type, []).append(entry.value)
    return groups


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
                'total_amount': Decimal('0'),
            }
        grouped[key]['items'].append(entry)
        amount = getattr(entry, 'amount', None)
        if amount is not None:
            grouped[key]['total_amount'] += Decimal(str(amount))

    for group in grouped.values():
        group['items'].sort(key=lambda item: getattr(item, date_attr), reverse=True)

    return sorted(grouped.values(), key=lambda group: group['date'], reverse=True)


def set_setting(key, value, description=None, commit=True):
    setting = AppSetting.query.filter_by(key=key).first()
    if not setting:
        setting = AppSetting(key=key, value=value, description=description)
        db.session.add(setting)
    else:
        setting.value = value
        if description is not None:
            setting.description = description
    if commit:
        db.session.commit()
    return setting


CLIENT_IP_MAX_LENGTH = 100


def client_ip():
    """Return the client address for logs.

    The proxy chain is resolved by the WSGI server (waitress ``--trusted-proxy``),
    so ``remote_addr`` is trusted; raw ``X-Forwarded-For`` is client-controlled.
    """
    value = (request.remote_addr or '').strip()
    return value[:CLIENT_IP_MAX_LENGTH] or None


ACTOR_LABEL_MAX_LENGTH = 64
EMERGENCY_ADMIN_LABEL = 'Аварийный администратор'


def actor_label_for(user):
    """Human-readable snapshot of the acting account for the audit log."""
    if not user:
        return None
    user_id = getattr(user, 'id', None)
    if user_id == 'admin':
        return EMERGENCY_ADMIN_LABEL
    name = getattr(user, 'username', None) or getattr(user, 'first_name', None) or 'Пользователь'
    label = f'{name} #{user_id}' if user_id is not None else str(name)
    return label[:ACTOR_LABEL_MAX_LENGTH]


def record_activity(action, user=None, entity_type=None, entity_id=None, description=None, ip_address=None, user_agent=None, impersonation_session_id=None, actor_label=None, commit=True):
    """Record an audit event; commit=False joins the caller's transaction."""
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
        actor_label=actor_label or actor_label_for(user),
        impersonation_session_id=impersonation_session_id,
    )
    db.session.add(log)
    if commit:
        db.session.commit()


def record_goal_activity(action, ref, transaction_id):
    """Journal a goal operation without amounts or goal names.

    The admin panel shows the journal and must not learn users' finances, so
    only the kind of object and its id are stored.
    """
    record_activity(
        action,
        current_user,
        entity_type=ref.entity_type,
        entity_id=transaction_id,
        description='Финансовая подушка' if ref.is_emergency else 'Финансовая цель',
        ip_address=client_ip(),
        user_agent=request.headers.get('User-Agent'),
    )


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
            if getattr(current_user, 'is_admin', False):
                # Admins stay inside the panel and see why the section is closed.
                return render_template('admin_forbidden.html'), 403
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapper
