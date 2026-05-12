from flask import Flask, render_template, request, jsonify, redirect, url_for, Response, abort
from flask_login import LoginManager, UserMixin, current_user, login_user, logout_user
from flask_migrate import Migrate
import re
from collections import OrderedDict
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from io import StringIO
import csv
from sqlalchemy import text, func
from extensions import db
from config import Config
import hashlib
import hmac
import time


login_manager = LoginManager()


class AdminUser(UserMixin):
    """Временный администратор для входа без Telegram"""

    def __init__(self):
        self.id = 'admin'
        self.username = 'admin'
        self.first_name = 'Администратор'
        self.role = 'superadmin'
        self.is_blocked = False

    @property
    def is_admin(self):
        return True

    @property
    def is_superadmin(self):
        return True

    def get_id(self):
        return str(self.id)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    Migrate(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    return app


app = create_app()
import legacy_models  # noqa: F401


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


app.jinja_env.filters['money'] = format_currency


@login_manager.user_loader
def load_user(user_id):
    from legacy_models import User
    if not user_id:
        return None
    if str(user_id) == 'admin':
        return AdminUser()
    try:
        return db.session.get(User, int(user_id))
    except (ValueError, TypeError):
        return None


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Требуется авторизация'}), 401
    return redirect(url_for('login'))


@app.before_request
def require_login():
    allowed_endpoints = {'static', 'login', 'telegram_login', 'logout', 'admin_login'}
    if request.endpoint in allowed_endpoints:
        return

    if getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'is_admin', False):
        if not request.path.startswith('/admin'):
            return redirect(url_for('admin_dashboard'))

    if getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'is_blocked', False):
        logout_user()
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Учетная запись заблокирована'}), 403
        return render_template('login.html', error='Ваша учётная запись заблокирована.', bot_username=app.config.get('TELEGRAM_BOT_USERNAME', ''), telegram_allowed=False)
    if not getattr(current_user, 'is_authenticated', False):
        return unauthorized()


@app.context_processor
def inject_user():
    app_name = get_setting('app_name', 'ДолгТрекер')
    bank_options = get_dictionary_values('bank')
    return dict(current_user=current_user, app_name=app_name, bank_options=bank_options)


# ──────────────────────────────────────────────────────────────
# Админские вспомогательные функции
# ──────────────────────────────────────────────────────────────


def get_setting(key, default=None):
    from legacy_models import AppSetting
    try:
        setting = AppSetting.query.filter_by(key=key).first()
        return setting.value if setting else default
    except Exception:
        return default


def get_dictionary_values(dictionary_type):
    from legacy_models import DictionaryEntry
    try:
        entries = DictionaryEntry.query.filter_by(dictionary_type=dictionary_type, is_active=True).order_by(DictionaryEntry.value.asc()).all()
        return [entry.value for entry in entries]
    except Exception:
        return []


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
    from legacy_models import AppSetting
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


def record_activity(action, user=None, entity_type=None, entity_id=None, description=None):
    from legacy_models import ActivityLog
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


# ──────────────────────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────────────────────


def parse_decimal(value, field_name, required=True):
    """Безопасно парсит десятичное число из строки"""
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
    """Безопасно парсит дату из строки"""
    if not value or str(value).strip() == '':
        if required:
            raise ValueError(f"Поле '{field_name}' обязательно для заполнения")
        return None
    try:
        return datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
    except ValueError:
        raise ValueError(f"Поле '{field_name}' содержит некорректную дату (формат: ГГГГ-ММ-ДД)")


def next_month_start(current_date):
    next_month = current_date.month % 12 + 1
    next_year = current_date.year + (current_date.month // 12)
    return date(next_year, next_month, 1)


def get_finance_summary(user_id, year=None, month=None):
    from legacy_models import Debt, Income, Expense, Payment

    today = date.today()
    use_selected_month = year is not None and month is not None
    if use_selected_month:
        try:
            month_start = date(int(year), int(month), 1)
        except (TypeError, ValueError):
            month_start = date(today.year, today.month, 1)
    else:
        month_start = date(today.year, today.month, 1)
    month_end = next_month_start(month_start)

    active_debts = Debt.query.filter_by(status='active', user_id=user_id).order_by(db.case((Debt.next_payment_date.is_(None), 1), else_=0), Debt.next_payment_date.asc()).all()
    total_remaining = sum(float(d.remaining_amount) for d in active_debts)
    total_original = sum(float(d.total_amount) for d in active_debts)

    incomes_this_month = Income.query.filter_by(user_id=user_id).filter(Income.income_date >= month_start, Income.income_date < month_end).all()
    expenses_this_month = Expense.query.filter_by(user_id=user_id).filter(Expense.expense_date >= month_start, Expense.expense_date < month_end).all()
    payments_this_month = Payment.query.join(Debt).filter(Debt.user_id == user_id, Payment.payment_date >= month_start, Payment.payment_date < month_end).all()

    if not use_selected_month and not (incomes_this_month or expenses_this_month or payments_this_month):
        latest_income = Income.query.with_entities(func.max(Income.income_date)).filter_by(user_id=user_id).scalar()
        latest_expense = Expense.query.with_entities(func.max(Expense.expense_date)).filter_by(user_id=user_id).scalar()
        latest_payment = Payment.query.join(Debt).with_entities(func.max(Payment.payment_date)).filter(Debt.user_id == user_id).scalar()
        latest_date = max(d for d in (latest_income, latest_expense, latest_payment) if d is not None) if any((latest_income, latest_expense, latest_payment)) else None
        if latest_date:
            month_start = date(latest_date.year, latest_date.month, 1)
            month_end = next_month_start(latest_date)
            incomes_this_month = Income.query.filter_by(user_id=user_id).filter(Income.income_date >= month_start, Income.income_date < month_end).all()
            expenses_this_month = Expense.query.filter_by(user_id=user_id).filter(Expense.expense_date >= month_start, Expense.expense_date < month_end).all()
            payments_this_month = Payment.query.join(Debt).filter(Debt.user_id == user_id, Payment.payment_date >= month_start, Payment.payment_date < month_end).all()

    total_incomes = float(Income.query.with_entities(func.coalesce(func.sum(Income.amount), 0)).filter_by(user_id=user_id).filter(Income.income_date >= month_start, Income.income_date < month_end).scalar() or 0)
    total_expenses = float(Expense.query.with_entities(func.coalesce(func.sum(Expense.amount), 0)).filter_by(user_id=user_id).filter(Expense.expense_date >= month_start, Expense.expense_date < month_end).scalar() or 0)
    total_payments = float(Payment.query.with_entities(func.coalesce(func.sum(Payment.amount), 0)).join(Debt).filter(Debt.user_id == user_id, Payment.payment_date >= month_start, Payment.payment_date < month_end).scalar() or 0)
    free_balance = total_incomes - total_expenses - total_payments
    overdue_count = len([d for d in active_debts if d.next_payment_date and d.next_payment_date < today])
    archived_count = Debt.query.filter_by(status='archived', user_id=user_id).count()
    total_debts = Debt.query.filter_by(user_id=user_id).count()

    if month_start.year == today.year and month_start.month == today.month:
        days_left = (month_end - today).days
    else:
        days_left = 0

    nearest_debt = next((d for d in active_debts if d.next_payment_date and d.next_payment_date >= today), None)

    return {
        'today': today,
        'month_start': month_start,
        'month_end': month_end,
        'active_debts': active_debts,
        'total_remaining': total_remaining,
        'total_original': total_original,
        'incomes_this_month': incomes_this_month,
        'expenses_this_month': expenses_this_month,
        'payments_this_month': payments_this_month,
        'total_incomes': total_incomes,
        'total_expenses': total_expenses,
        'total_payments': total_payments,
        'free_balance': free_balance,
        'days_left': days_left,
        'nearest_debt': nearest_debt,
        'overdue_count': overdue_count,
        'archived_count': archived_count,
        'total_debts': total_debts,
        'selected_year': month_start.year,
        'selected_month': month_start.month,
    }


def verify_telegram_login(data):
    bot_token = app.config.get('TELEGRAM_BOT_TOKEN', '')
    if not bot_token:
        return False

    auth_date = data.get('auth_date')
    hash_value = data.get('hash')
    if not auth_date or not hash_value:
        return False

    try:
        auth_timestamp = int(auth_date)
    except ValueError:
        return False

    if auth_timestamp > time.time() + 300:
        return False
    if time.time() - auth_timestamp > 86400:
        return False

    check_data = []
    for key, value in data.items():
        if key == 'hash':
            continue
        check_data.append(f'{key}={value}')
    check_data.sort()
    data_check_string = '\n'.join(check_data)

    secret_key = hashlib.sha256(bot_token.encode('utf-8')).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_hash, hash_value)


# ──────────────────────────────────────────────────────────────
# Аутентификация
# ──────────────────────────────────────────────────────────────


@app.route('/login')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    telegram_allowed = get_setting('telegram_login_enabled', app.config.get('TELEGRAM_LOGIN_ENABLED', 'true'))
    if isinstance(telegram_allowed, str):
        telegram_allowed = telegram_allowed.lower() in ('1', 'true', 'yes', 'on')
    return render_template(
        'login.html',
        bot_username=app.config.get('TELEGRAM_BOT_USERNAME', ''),
        telegram_allowed=telegram_allowed,
        admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False),
    )


@app.route('/telegram-login')
def telegram_login():
    data = request.args.to_dict()
    bot_username = app.config.get('TELEGRAM_BOT_USERNAME', '')

    telegram_allowed = get_setting('telegram_login_enabled', app.config.get('TELEGRAM_LOGIN_ENABLED', 'true'))
    if isinstance(telegram_allowed, str):
        telegram_allowed = telegram_allowed.lower() in ('1', 'true', 'yes', 'on')
    if not telegram_allowed:
        return render_template('login.html', error='Вход через Telegram временно отключён администратором.', bot_username=bot_username, telegram_allowed=False, admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False))

    if not verify_telegram_login(data):
        return render_template('login.html', error='Ошибка авторизации через Telegram. Проверьте настройки бота.', bot_username=bot_username, telegram_allowed=telegram_allowed, admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False))

    from legacy_models import User
    telegram_id = data.get('id')
    if not telegram_id:
        return render_template('login.html', error='Неверные данные авторизации.', bot_username=bot_username, telegram_allowed=telegram_allowed, admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False))

    user = User.query.filter_by(telegram_id=int(telegram_id)).first()
    auth_date = datetime.utcfromtimestamp(int(data.get('auth_date', '0')))
    if not user:
        user = User(
            telegram_id=int(telegram_id),
            username=data.get('username'),
            first_name=data.get('first_name'),
            last_name=data.get('last_name'),
            photo_url=data.get('photo_url'),
            auth_date=auth_date,
        )
        # Первому пользователю назначаем супер-админа, чтобы админка была доступна
        if not User.query.filter(User.role.in_(['admin', 'superadmin'])).first():
            user.role = 'superadmin'
        db.session.add(user)
    else:
        if user.is_blocked:
            return render_template('login.html', error='Ваш аккаунт заблокирован. Обратитесь к администратору.', bot_username=bot_username, telegram_allowed=False, admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False))
        user.username = data.get('username')
        user.first_name = data.get('first_name')
        user.last_name = data.get('last_name')
        user.photo_url = data.get('photo_url')
        user.auth_date = auth_date

    user.login_count = (user.login_count or 0) + 1
    user.last_login_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    user.last_user_agent = request.headers.get('User-Agent')

    db.session.commit()
    login_user(user)
    return redirect(url_for('index'))


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
        return redirect(url_for('admin_dashboard'))

    admin_login_enabled = app.config.get('ADMIN_LOGIN_ENABLED', False)
    admin_password = app.config.get('ADMIN_PASSWORD', '')
    error_message = None

    if request.method == 'POST':
        if not admin_login_enabled or not admin_password:
            error_message = 'Админ-вход отключён или пароль не настроен.'
        else:
            password = request.form.get('password', '')
            if password == admin_password:
                login_user(AdminUser())
                return redirect(url_for('admin_dashboard'))
            error_message = 'Неверный пароль администратора.'

    return render_template('admin_login.html', admin_login_enabled=admin_login_enabled, error_message=error_message)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))


# ──────────────────────────────────────────────────────────────# Админская панель
# ──────────────────────────────────────────────────────────────


@app.route('/admin')
@admin_required
def admin_dashboard():
    from legacy_models import User, Debt, Payment, ActivityLog, DictionaryEntry
    stats = {
        'users': User.query.count(),
        'debts': Debt.query.count(),
        'payments': Payment.query.count(),
        'logs': ActivityLog.query.count(),
        'dictionary_entries': DictionaryEntry.query.count(),
    }
    return render_template('admin_dashboard.html', stats=stats)


@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    from legacy_models import AppSetting
    success_message = None

    if request.method == 'POST':
        for key in DEFAULT_SETTINGS.keys():
            value = request.form.get(key, '').strip()
            if key in ('registration_enabled', 'telegram_login_enabled', 'archive_enabled', 'export_enabled', 'overdue_after_date'):
                value = 'true' if value == 'on' or value.lower() in ('1', 'true', 'yes', 'on') else 'false'
            if not value:
                value = DEFAULT_SETTINGS.get(key, '')
            set_setting(key, value)
        record_activity('Изменил настройки приложения', current_user, description='Обновлены системные настройки')
        return redirect(url_for('admin_settings', success='Настройки сохранены'))

    settings = {key: get_setting(key, DEFAULT_SETTINGS[key]) for key in DEFAULT_SETTINGS}
    return render_template('admin_settings.html', settings=settings, success_message=request.args.get('success'))


@app.route('/admin/dictionaries', methods=['GET', 'POST'])
@admin_required
def admin_dictionaries():
    from legacy_models import DictionaryEntry
    error_message = None
    success_message = request.args.get('success')

    if request.method == 'POST':
        dictionary_type = request.form.get('dictionary_type')
        value = str(request.form.get('value', '')).strip()
        if not dictionary_type or dictionary_type not in [item[0] for item in DICTIONARY_TYPES]:
            error_message = 'Выберите тип справочника'
        elif not value:
            error_message = 'Значение не может быть пустым'
        else:
            try:
                entry = DictionaryEntry(dictionary_type=dictionary_type, value=value)
                db.session.add(entry)
                db.session.commit()
                record_activity('Добавил элемент справочника', current_user, entity_type=dictionary_type, description=value)
                return redirect(url_for('admin_dictionaries', success='Элемент добавлен'))
            except Exception as e:
                db.session.rollback()
                error_message = f'Не удалось сохранить элемент: {str(e)}'

    entries = DictionaryEntry.query.order_by(DictionaryEntry.dictionary_type.asc(), DictionaryEntry.value.asc()).all()
    type_labels = {key: label for key, label in DICTIONARY_TYPES}
    return render_template('admin_dictionaries.html', entries=entries, types=DICTIONARY_TYPES, type_labels=type_labels, error_message=error_message, success_message=success_message)


@app.route('/admin/dictionaries/<int:entry_id>/delete', methods=['POST'])
@admin_required
def admin_delete_dictionary_entry(entry_id):
    from legacy_models import DictionaryEntry
    entry = DictionaryEntry.query.get(entry_id)
    if entry:
        db.session.delete(entry)
        db.session.commit()
        record_activity('Удалил элемент справочника', current_user, entity_type=entry.dictionary_type, entity_id=entry.id, description=entry.value)
        return redirect(url_for('admin_dictionaries', success='Элемент удалён'))
    return redirect(url_for('admin_dictionaries', success='Элемент не найден'))


@app.route('/admin/users')
@admin_required
def admin_users():
    from legacy_models import User
    users = User.query.order_by(User.role.desc(), User.created_at.desc()).all()
    return render_template('admin_users.html', users=users)


@app.route('/admin/impersonate/test', methods=['POST'])
@admin_required
def admin_impersonate_test():
    from legacy_models import User
    test_user = User.query.filter_by(username='test').first()
    if not test_user:
        test_user = User(
            telegram_id=-999999999,
            username='test',
            first_name='test',
            last_name=None,
            auth_date=datetime.utcnow(),
            role='user',
            is_blocked=False,
            login_count=0,
        )
        db.session.add(test_user)
        db.session.commit()

    if test_user.is_blocked:
        test_user.is_blocked = False
        db.session.commit()

    logout_user()
    login_user(test_user)
    return redirect(url_for('index'))


@app.route('/admin/impersonate/<int:user_id>', methods=['POST'])
@admin_required
def admin_impersonate_user(user_id):
    from legacy_models import User
    user = User.query.get_or_404(user_id)
    if user.is_blocked:
        return redirect(url_for('admin_users'))

    logout_user()
    login_user(user)
    return redirect(url_for('index'))


@app.route('/admin/users/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def admin_user_detail(user_id):
    from legacy_models import User, Debt, Payment, ActivityLog
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'block':
            user.is_blocked = True
            record_activity('Заблокировал пользователя', current_user, entity_type='user', entity_id=user.id, description=f'Пользователь {user.telegram_id}')
        elif action == 'unblock':
            user.is_blocked = False
            record_activity('Разблокировал пользователя', current_user, entity_type='user', entity_id=user.id, description=f'Пользователь {user.telegram_id}')
        elif action in ('make_admin', 'make_superadmin', 'make_user'):
            if not current_user.is_superadmin:
                return redirect(url_for('admin_user_detail', user_id=user.id))
            if action == 'make_admin':
                user.role = 'admin'
                record_activity('Назначил администратора', current_user, entity_type='user', entity_id=user.id)
            elif action == 'make_superadmin':
                user.role = 'superadmin'
                record_activity('Назначил супер-администратора', current_user, entity_type='user', entity_id=user.id)
            else:
                user.role = 'user'
                record_activity('Снял административные права', current_user, entity_type='user', entity_id=user.id)
        elif action == 'delete':
            if not current_user.is_superadmin:
                return redirect(url_for('admin_user_detail', user_id=user.id))
            record_activity('Удалил пользователя', current_user, entity_type='user', entity_id=user.id)
            db.session.delete(user)
            db.session.commit()
            return redirect(url_for('admin_users'))
        db.session.commit()
        return redirect(url_for('admin_user_detail', user_id=user.id, success='Действие выполнено'))

    active_debts = Debt.query.filter_by(user_id=user.id, status='active').count()
    archived_debts = Debt.query.filter_by(user_id=user.id, status='archived').count()
    payments = Payment.query.join(Debt).filter(Debt.user_id == user.id).count()
    recent_actions = ActivityLog.query.filter_by(user_id=user.id).order_by(ActivityLog.created_at.desc()).limit(20).all()
    return render_template('admin_user_detail.html', user=user, active_debts=active_debts, archived_debts=archived_debts, payments=payments, recent_actions=recent_actions, success_message=request.args.get('success'))


@app.route('/admin/logs')
@admin_required
def admin_logs():
    from legacy_models import ActivityLog
    logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(100).all()
    return render_template('admin_logs.html', logs=logs)


@app.route('/admin/export')
@admin_required
def admin_export():
    from legacy_models import Debt, Payment
    debts = Debt.query.order_by(Debt.created_at.desc()).limit(100).all()
    payments = Payment.query.order_by(Payment.payment_date.desc()).limit(100).all()
    return render_template('admin_export.html', debts=debts, payments=payments)


@app.route('/admin/export/<string:export_type>.csv')
@admin_required
def admin_export_csv(export_type):
    from legacy_models import User, Debt, Payment
    output = StringIO()
    writer = csv.writer(output)

    if export_type == 'users':
        writer.writerow(['id', 'telegram_id', 'username', 'first_name', 'last_name', 'role', 'is_blocked', 'login_count', 'last_login_ip', 'last_user_agent', 'created_at'])
        for user in User.query.order_by(User.id.asc()).all():
            writer.writerow([
                user.id,
                user.telegram_id,
                user.username,
                user.first_name,
                user.last_name,
                user.role,
                'yes' if user.is_blocked else 'no',
                user.login_count,
                user.last_login_ip,
                user.last_user_agent,
                user.created_at.strftime('%Y-%m-%d %H:%M:%S') if user.created_at else '',
            ])
        filename = 'users.csv'
    elif export_type == 'debts':
        writer.writerow(['id', 'user_id', 'bank_name', 'debt_type', 'product_name', 'total_amount', 'remaining_amount', 'status', 'next_payment_date', 'created_at', 'updated_at'])
        for debt in Debt.query.order_by(Debt.id.asc()).all():
            writer.writerow([
                debt.id,
                debt.user_id,
                debt.bank_name,
                debt.debt_type,
                debt.product_name,
                float(debt.total_amount),
                float(debt.remaining_amount),
                debt.status,
                debt.next_payment_date.strftime('%Y-%m-%d') if debt.next_payment_date else '',
                debt.created_at.strftime('%Y-%m-%d %H:%M:%S') if debt.created_at else '',
                debt.updated_at.strftime('%Y-%m-%d %H:%M:%S') if debt.updated_at else '',
            ])
        filename = 'debts.csv'
    elif export_type == 'payments':
        writer.writerow(['id', 'debt_id', 'amount', 'payment_date', 'remaining_after_payment', 'comment', 'created_at'])
        for payment in Payment.query.order_by(Payment.id.asc()).all():
            writer.writerow([
                payment.id,
                payment.debt_id,
                float(payment.amount),
                payment.payment_date.strftime('%Y-%m-%d') if payment.payment_date else '',
                float(payment.remaining_after_payment),
                payment.comment,
                payment.created_at.strftime('%Y-%m-%d %H:%M:%S') if payment.created_at else '',
            ])
        filename = 'payments.csv'
    else:
        return redirect(url_for('admin_export'))

    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response


# ──────────────────────────────────────────────────────────────# Основные страницы
# ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Главный дашборд"""
    from legacy_models import Debt, Payment, Income, Expense
    summary = get_finance_summary(current_user.id)
    total_incomes = summary['total_incomes']
    total_expenses = summary['total_expenses']
    total_payments = summary['total_payments']
    free_balance = summary['free_balance']
    days_left = summary['days_left']
    daily_budget = free_balance / days_left if days_left > 0 else 0
    total_remaining = summary['total_remaining']
    total_original = summary['total_original']
    nearest_debt = summary['nearest_debt']
    overdue_count = summary['overdue_count']
    today = summary['today']
    month_start = summary['month_start']
    active_debts = summary['active_debts']
    
    # Ближайший платеж
    upcoming = [d for d in active_debts if d.next_payment_date and d.next_payment_date >= today]
    nearest_debt = upcoming[0] if upcoming else None
    
    # Просроченные
    overdue_count = len([d for d in active_debts if d.next_payment_date and d.next_payment_date < today])
    
    return render_template('index.html',
        debts=active_debts,
        total_remaining=total_remaining,
        total_original=total_original,
        active_count=len(active_debts),
        nearest_debt=nearest_debt,
        overdue_count=overdue_count,
        today=today,
        month_start=month_start,
        total_incomes=total_incomes,
        total_expenses=total_expenses,
        total_payments=total_payments,
        free_balance=free_balance,
        days_left=days_left,
        daily_budget=daily_budget,
    )


@app.route('/finance')
def finance():
    selected_year = request.args.get('year', type=int)
    selected_month = request.args.get('month', type=int)
    summary = get_finance_summary(current_user.id, selected_year, selected_month)

    today = date.today()
    year_options = [today.year - i for i in range(0, 5)]
    if summary['selected_year'] not in year_options:
        year_options.append(summary['selected_year'])
        year_options.sort(reverse=True)

    month_names = [
        'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
        'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
    ]
    month_options = list(enumerate(month_names, start=1))

    return render_template('finance.html',
        active_count=len(summary['active_debts']),
        total_remaining=summary['total_remaining'],
        total_original=summary['total_original'],
        nearest_debt=summary['nearest_debt'],
        overdue_count=summary['overdue_count'],
        today=summary['today'],
        month_start=summary['month_start'],
        total_incomes=summary['total_incomes'],
        total_expenses=summary['total_expenses'],
        total_payments=summary['total_payments'],
        free_balance=summary['free_balance'],
        days_left=summary['days_left'],
        daily_budget=(summary['free_balance'] / summary['days_left'] if summary['days_left'] > 0 else 0),
        archived_count=summary['archived_count'],
        total_debts=summary['total_debts'],
        incomes_this_month=summary['incomes_this_month'],
        expenses_this_month=summary['expenses_this_month'],
        payments_this_month=summary['payments_this_month'],
        selected_year=summary['selected_year'],
        selected_month=summary['selected_month'],
        year_options=year_options,
        month_options=month_options,
    )


def get_user_debt(debt_id):
    from legacy_models import Debt
    return Debt.query.filter_by(id=debt_id, user_id=current_user.id).first()


@app.route('/archive')
def archive():
    """Страница архива"""
    from legacy_models import Debt
    archived_debts = Debt.query.filter_by(status='archived', user_id=current_user.id).order_by(Debt.updated_at.desc()).all()
    return render_template('archive.html', debts=archived_debts)


@app.route('/incomes', methods=['GET', 'POST'])
def incomes():
    from legacy_models import Income
    error_message = None
    success_message = request.args.get('success')

    if request.method == 'POST':
        try:
            amount = parse_decimal(request.form.get('amount'), 'Сумма', required=True)
            category = request.form.get('category')
            if category not in [item[0] for item in INCOME_CATEGORIES]:
                raise ValueError('Выберите корректную категорию дохода')
            income_date = parse_date(request.form.get('income_date'), 'Дата', required=True)
            source = str(request.form.get('source', '')).strip() or None
            comment = str(request.form.get('comment', '')).strip() or None

            income = Income(
                user_id=current_user.id,
                amount=amount,
                category=category,
                source=source,
                income_date=income_date,
                comment=comment,
            )
            db.session.add(income)
            db.session.commit()
            return redirect(url_for('incomes', success='Доход сохранён'))
        except ValueError as e:
            error_message = str(e)
        except Exception as e:
            db.session.rollback()
            error_message = 'Ошибка сервера: ' + str(e)

    incomes = Income.query.filter_by(user_id=current_user.id).order_by(Income.income_date.desc()).all()
    groups = group_entries_by_month(incomes, 'income_date')
    active_month = date.today().strftime('%Y-%m')
    if groups and active_month not in [group['year_month'] for group in groups]:
        active_month = groups[0]['year_month']
    return render_template('incomes.html', incomes=incomes, groups=groups,
                           active_month=active_month, categories=INCOME_CATEGORIES,
                           success_message=success_message, error_message=error_message,
                           edit_income=None)


@app.route('/incomes/edit/<int:income_id>', methods=['GET', 'POST'])
def edit_income(income_id):
    from legacy_models import Income
    error_message = None
    success_message = request.args.get('success')
    income = Income.query.filter_by(id=income_id, user_id=current_user.id).first()
    if not income:
        abort(404)

    if request.method == 'POST':
        try:
            amount = parse_decimal(request.form.get('amount'), 'Сумма', required=True)
            category = request.form.get('category')
            if category not in [item[0] for item in INCOME_CATEGORIES]:
                raise ValueError('Выберите корректную категорию дохода')
            income_date = parse_date(request.form.get('income_date'), 'Дата', required=True)
            source = str(request.form.get('source', '')).strip() or None
            comment = str(request.form.get('comment', '')).strip() or None

            income.amount = amount
            income.category = category
            income.source = source
            income.income_date = income_date
            income.comment = comment
            db.session.commit()
            return redirect(url_for('incomes', success='Доход обновлён'))
        except ValueError as e:
            error_message = str(e)
        except Exception as e:
            db.session.rollback()
            error_message = 'Ошибка сервера: ' + str(e)

    incomes = Income.query.filter_by(user_id=current_user.id).order_by(Income.income_date.desc()).all()
    groups = group_entries_by_month(incomes, 'income_date')
    return render_template('incomes.html', incomes=incomes, groups=groups,
                           active_month=date.today().strftime('%Y-%m'), categories=INCOME_CATEGORIES,
                           success_message=success_message, error_message=error_message,
                           edit_income=income)


@app.route('/incomes/delete/<int:income_id>', methods=['POST'])
def delete_income(income_id):
    from legacy_models import Income
    income = Income.query.filter_by(id=income_id, user_id=current_user.id).first()
    if not income:
        abort(404)
    db.session.delete(income)
    db.session.commit()
    return redirect(url_for('incomes', success='Доход удалён'))


@app.route('/expenses', methods=['GET', 'POST'])
def expenses():
    from legacy_models import Expense
    error_message = None
    success_message = request.args.get('success')

    if request.method == 'POST':
        try:
            amount = parse_decimal(request.form.get('amount'), 'Сумма', required=True)
            category = request.form.get('category')
            if category not in [item[0] for item in EXPENSE_CATEGORIES]:
                raise ValueError('Выберите корректную категорию расхода')
            title = str(request.form.get('title', '')).strip()
            if not title:
                raise ValueError('Название расхода обязательно')
            expense_date = parse_date(request.form.get('expense_date'), 'Дата', required=True)
            payment_method = str(request.form.get('payment_method', '')).strip() or None
            comment = str(request.form.get('comment', '')).strip() or None

            expense = Expense(
                user_id=current_user.id,
                amount=amount,
                category=category,
                title=title,
                expense_date=expense_date,
                payment_method=payment_method,
                comment=comment,
            )
            db.session.add(expense)
            db.session.commit()
            return redirect(url_for('expenses', success='Расход сохранён'))
        except ValueError as e:
            error_message = str(e)
        except Exception as e:
            db.session.rollback()
            error_message = 'Ошибка сервера: ' + str(e)

    expenses = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.expense_date.desc()).all()
    groups = group_entries_by_month(expenses, 'expense_date')
    active_month = date.today().strftime('%Y-%m')
    if groups and active_month not in [group['year_month'] for group in groups]:
        active_month = groups[0]['year_month']
    return render_template('expenses.html', expenses=expenses, groups=groups,
                           active_month=active_month, categories=EXPENSE_CATEGORIES,
                           payment_methods=PAYMENT_METHODS, success_message=success_message,
                           error_message=error_message, edit_expense=None)


@app.route('/expenses/edit/<int:expense_id>', methods=['GET', 'POST'])
def edit_expense(expense_id):
    from legacy_models import Expense
    error_message = None
    success_message = request.args.get('success')
    expense = Expense.query.filter_by(id=expense_id, user_id=current_user.id).first()
    if not expense:
        abort(404)

    if request.method == 'POST':
        try:
            amount = parse_decimal(request.form.get('amount'), 'Сумма', required=True)
            category = request.form.get('category')
            if category not in [item[0] for item in EXPENSE_CATEGORIES]:
                raise ValueError('Выберите корректную категорию расхода')
            title = str(request.form.get('title', '')).strip()
            if not title:
                raise ValueError('Название расхода обязательно')
            expense_date = parse_date(request.form.get('expense_date'), 'Дата', required=True)
            payment_method = str(request.form.get('payment_method', '')).strip() or None
            comment = str(request.form.get('comment', '')).strip() or None

            expense.amount = amount
            expense.category = category
            expense.title = title
            expense.expense_date = expense_date
            expense.payment_method = payment_method
            expense.comment = comment
            db.session.commit()
            return redirect(url_for('expenses', success='Расход обновлён'))
        except ValueError as e:
            error_message = str(e)
        except Exception as e:
            db.session.rollback()
            error_message = 'Ошибка сервера: ' + str(e)

    expenses = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.expense_date.desc()).all()
    groups = group_entries_by_month(expenses, 'expense_date')
    return render_template('expenses.html', expenses=expenses, groups=groups,
                           active_month=date.today().strftime('%Y-%m'), categories=EXPENSE_CATEGORIES,
                           payment_methods=PAYMENT_METHODS, success_message=success_message,
                           error_message=error_message, edit_expense=expense)


@app.route('/expenses/delete/<int:expense_id>', methods=['POST'])
def delete_expense(expense_id):
    from legacy_models import Expense
    expense = Expense.query.filter_by(id=expense_id, user_id=current_user.id).first()
    if not expense:
        abort(404)
    db.session.delete(expense)
    db.session.commit()
    return redirect(url_for('expenses', success='Расход удалён'))


# ──────────────────────────────────────────────────────────────
# API для долгов
# ──────────────────────────────────────────────────────────────

@app.route('/api/debts', methods=['GET'])
def api_get_debts():
    """Получить список долгов с фильтрацией"""
    from legacy_models import Debt
    status = request.args.get('status', 'active')
    bank_filter = request.args.get('bank', '').strip()
    type_filter = request.args.get('type', '').strip()

    query = Debt.query.filter_by(status=status, user_id=current_user.id)
    if bank_filter:
        query = query.filter(Debt.bank_name.ilike(f'%{bank_filter}%'))
    if type_filter:
        query = query.filter_by(debt_type=type_filter)

    debts = query.order_by(db.case((Debt.next_payment_date.is_(None), 1), else_=0), Debt.next_payment_date.asc()).all()
    return jsonify({'success': True, 'debts': [d.to_dict() for d in debts]})


@app.route('/api/debts', methods=['POST'])
def api_create_debt():
    """Создать новый долг"""
    from legacy_models import Debt
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Нет данных'}), 400

    try:
        # Валидация обязательных полей
        bank_name = str(data.get('bank_name', '')).strip()
        if not bank_name:
            raise ValueError("Название банка обязательно")

        debt_type = str(data.get('debt_type', '')).strip()
        if debt_type not in ('credit_card', 'split', 'mortgage'):
            raise ValueError("Тип долга: выберите 'credit_card', 'split' или 'mortgage'")

        product_name = str(data.get('product_name', '')).strip()
        if not product_name:
            raise ValueError("Название продукта/карты обязательно")

        total_amount = parse_decimal(data.get('total_amount'), 'Сумма долга', required=True)
        remaining_amount = parse_decimal(data.get('remaining_amount'), 'Остаток долга', required=True)
        minimum_payment = parse_decimal(data.get('minimum_payment'), 'Минимальный платеж', required=False)
        interest_rate = parse_decimal(data.get('interest_rate'), 'Процентная ставка', required=False)
        next_payment_date = parse_date(data.get('next_payment_date'), 'Дата следующего платежа')

        if remaining_amount > total_amount:
            raise ValueError("Остаток долга не может превышать общую сумму")

        debt = Debt(
            user_id=current_user.id,
            bank_name=bank_name,
            debt_type=debt_type,
            product_name=product_name,
            total_amount=total_amount,
            remaining_amount=remaining_amount,
            minimum_payment=minimum_payment,
            interest_rate=interest_rate,
            next_payment_date=next_payment_date,
            comment=str(data.get('comment', '')).strip() or None,
            status='active',
        )
        db.session.add(debt)
        db.session.commit()
        return jsonify({'success': True, 'debt': debt.to_dict()}), 201

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 422
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Ошибка сервера: {str(e)}'}), 500


@app.route('/api/debts/<int:debt_id>', methods=['GET'])
def api_get_debt(debt_id):
    """Получить один долг"""
    debt = get_user_debt(debt_id)
    if not debt:
        return jsonify({'success': False, 'error': 'Долг не найден'}), 404
    return jsonify({'success': True, 'debt': debt.to_dict()})


@app.route('/api/debts/<int:debt_id>', methods=['PUT'])
def api_update_debt(debt_id):
    """Обновить долг"""
    debt = get_user_debt(debt_id)
    if not debt:
        return jsonify({'success': False, 'error': 'Долг не найден'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Нет данных'}), 400

    try:
        if 'bank_name' in data:
            bank_name = str(data['bank_name']).strip()
            if not bank_name:
                raise ValueError("Название банка обязательно")
            debt.bank_name = bank_name

        if 'debt_type' in data:
            if data['debt_type'] not in ('credit_card', 'split', 'mortgage'):
                raise ValueError("Некорректный тип долга")
            debt.debt_type = data['debt_type']

        if 'product_name' in data:
            product_name = str(data['product_name']).strip()
            if not product_name:
                raise ValueError("Название продукта обязательно")
            debt.product_name = product_name

        if 'total_amount' in data:
            debt.total_amount = parse_decimal(data['total_amount'], 'Сумма долга', required=True)
        if 'remaining_amount' in data:
            debt.remaining_amount = parse_decimal(data['remaining_amount'], 'Остаток долга', required=True)
        if 'minimum_payment' in data:
            debt.minimum_payment = parse_decimal(data['minimum_payment'], 'Минимальный платеж', required=False)
        if 'interest_rate' in data:
            debt.interest_rate = parse_decimal(data['interest_rate'], 'Процентная ставка', required=False)
        if 'next_payment_date' in data:
            debt.next_payment_date = parse_date(data['next_payment_date'], 'Дата следующего платежа')
        if 'comment' in data:
            debt.comment = str(data['comment']).strip() or None

        if float(debt.remaining_amount) > float(debt.total_amount):
            raise ValueError("Остаток долга не может превышать общую сумму")

        debt.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'debt': debt.to_dict()})

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 422
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Ошибка сервера: {str(e)}'}), 500


@app.route('/api/debts/<int:debt_id>/archive', methods=['POST'])
def api_archive_debt(debt_id):
    """Архивировать долг"""
    debt = get_user_debt(debt_id)
    if not debt:
        return jsonify({'success': False, 'error': 'Долг не найден'}), 404

    debt.status = 'archived'
    debt.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'message': 'Карточка перемещена в архив'})


@app.route('/api/debts/<int:debt_id>/restore', methods=['POST'])
def api_restore_debt(debt_id):
    """Восстановить долг из архива"""
    debt = get_user_debt(debt_id)
    if not debt:
        return jsonify({'success': False, 'error': 'Долг не найден'}), 404

    debt.status = 'active'
    debt.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'message': 'Карточка восстановлена', 'debt': debt.to_dict()})


@app.route('/api/debts/<int:debt_id>/delete', methods=['DELETE'])
def api_delete_debt(debt_id):
    """Удалить долг полностью"""
    debt = get_user_debt(debt_id)
    if not debt:
        return jsonify({'success': False, 'error': 'Долг не найден'}), 404

    db.session.delete(debt)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Карточка удалена'})


# ──────────────────────────────────────────────────────────────
# API для платежей
# ──────────────────────────────────────────────────────────────

@app.route('/api/debts/<int:debt_id>/payments', methods=['GET'])
def api_get_payments(debt_id):
    """История платежей по долгу"""
    from legacy_models import Payment
    debt = get_user_debt(debt_id)
    if not debt:
        return jsonify({'success': False, 'error': 'Долг не найден'}), 404

    payments = Payment.query.filter_by(debt_id=debt_id).order_by(Payment.payment_date.desc()).all()
    return jsonify({
        'success': True,
        'debt': debt.to_dict(),
        'payments': [p.to_dict() for p in payments]
    })


@app.route('/api/debts/<int:debt_id>/payments', methods=['POST'])
def api_add_payment(debt_id):
    """Внести платеж"""
    from legacy_models import Payment
    debt = get_user_debt(debt_id)
    if not debt:
        return jsonify({'success': False, 'error': 'Долг не найден'}), 404
    if debt.status != 'active':
        return jsonify({'success': False, 'error': 'Нельзя вносить платеж в архивный долг'}), 422

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Нет данных'}), 400

    try:
        amount = parse_decimal(data.get('amount'), 'Сумма платежа', required=True)
        if amount <= 0:
            raise ValueError("Сумма платежа должна быть больше нуля")

        payment_date_str = data.get('payment_date')
        if payment_date_str:
            payment_date = parse_date(payment_date_str, 'Дата платежа')
        else:
            payment_date = date.today()

        # Вычисляем новый остаток
        new_remaining = max(Decimal('0'), debt.remaining_amount - amount)
        
        # Сохраняем платеж
        payment = Payment(
            debt_id=debt_id,
            amount=amount,
            payment_date=payment_date,
            comment=str(data.get('comment', '')).strip() or None,
            remaining_after_payment=new_remaining,
        )
        debt.remaining_amount = new_remaining
        debt.updated_at = datetime.utcnow()

        db.session.add(payment)
        db.session.commit()

        # Проверяем, погашен ли долг
        debt_cleared = float(new_remaining) <= 0.01

        return jsonify({
            'success': True,
            'payment': payment.to_dict(),
            'debt': debt.to_dict(),
            'debt_cleared': debt_cleared,
        })

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 422
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Ошибка сервера: {str(e)}'}), 500


# ──────────────────────────────────────────────────────────────
# Инициализация базы данных и тестовые данные
# ──────────────────────────────────────────────────────────────

@app.route('/api/init-db', methods=['POST'])
def init_db_route():
    """Инициализация БД и загрузка тестовых данных"""
    from legacy_models import Debt, Payment, Income, Expense
    
    # Добавляем тестовые данные только если БД пустая
    if (
        Debt.query.filter_by(user_id=current_user.id).count() == 0
        and Income.query.filter_by(user_id=current_user.id).count() == 0
        and Expense.query.filter_by(user_id=current_user.id).count() == 0
    ):
        seed_data()
    
    return jsonify({'success': True, 'message': 'База данных инициализирована'})


def seed_data():
    """Добавление тестовых данных"""
    from legacy_models import Debt, Payment, Income, Expense
    today = date.today()

    def shift_month(base_date, offset):
        month = base_date.month + offset
        year = base_date.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return year, month

    def next_payment_day(day, threshold_day):
        if today.day < threshold_day:
            return date(today.year, today.month, day)
        year, month = shift_month(today, 1)
        return date(year, month, day)

    def previous_month_day(day, offset):
        year, month = shift_month(today, offset)
        return date(year, month, day)

    debts = [
        Debt(
            user_id=current_user.id,
            bank_name='Тинькофф',
            debt_type='credit_card',
            product_name='Тинькофф Платинум',
            total_amount=Decimal('85000'),
            remaining_amount=Decimal('47500'),
            minimum_payment=Decimal('3200'),
            interest_rate=Decimal('28.9'),
            next_payment_date=next_payment_day(25, 25),
            comment='Основная кредитная карта',
            status='active',
        ),
        Debt(
            user_id=current_user.id,
            bank_name='Сбербанк',
            debt_type='split',
            product_name='СберСплит — MacBook Pro',
            total_amount=Decimal('180000'),
            remaining_amount=Decimal('120000'),
            minimum_payment=Decimal('15000'),
            interest_rate=None,
            next_payment_date=next_payment_day(15, 15),
            comment='12 платежей, прошло 4',
            status='active',
        ),
        Debt(
            user_id=current_user.id,
            bank_name='Альфа-Банк',
            debt_type='credit_card',
            product_name='Альфа-Карта',
            total_amount=Decimal('50000'),
            remaining_amount=Decimal('8200'),
            minimum_payment=Decimal('1500'),
            interest_rate=Decimal('24.5'),
            next_payment_date=(today + timedelta(days=3)),
            comment='Почти погашена',
            status='active',
        ),
        Debt(
            user_id=current_user.id,
            bank_name='ВТБ',
            debt_type='split',
            product_name='ВТБ Части — iPhone 15',
            total_amount=Decimal('95000'),
            remaining_amount=Decimal('0'),
            minimum_payment=Decimal('0'),
            interest_rate=None,
            next_payment_date=None,
            comment='Полностью погашено',
            status='archived',
        ),
    ]

    for debt in debts:
        db.session.add(debt)
    db.session.flush()

    # Тестовые платежи для первого долга
    payments = [
        Payment(
            debt_id=debts[0].id,
            amount=Decimal('10000'),
            payment_date=previous_month_day(25, -1),
            comment='Плановый платеж',
            remaining_after_payment=Decimal('57500')
        ),
        Payment(
            debt_id=debts[0].id,
            amount=Decimal('20000'),
            payment_date=previous_month_day(25, -2),
            comment='Досрочный платеж',
            remaining_after_payment=Decimal('67500')
        ),
    ]
    for p in payments:
        db.session.add(p)

    incomes = [
        Income(
            user_id=current_user.id,
            amount=Decimal('85000'),
            category='salary',
            source='Основная работа',
            income_date=date(today.year, today.month, 10),
            comment='Зарплата за прошлый месяц',
        ),
        Income(
            user_id=current_user.id,
            amount=Decimal('15000'),
            category='bonus',
            source='Премия',
            income_date=date(today.year, today.month, 5),
            comment='Премия за выполнение плана',
        ),
    ]
    for inc in incomes:
        db.session.add(inc)

    expenses = [
        Expense(
            user_id=current_user.id,
            amount=Decimal('3500'),
            category='products',
            title='Продукты',
            expense_date=date(today.year, today.month, 12),
            payment_method='card',
            comment='Покупка в магазине',
        ),
        Expense(
            user_id=current_user.id,
            amount=Decimal('6200'),
            category='transport',
            title='Транспорт',
            expense_date=date(today.year, today.month, 7),
            payment_method='card',
            comment='Проезд и такси',
        ),
    ]
    for exp in expenses:
        db.session.add(exp)

    db.session.commit()


# ──────────────────────────────────────────────────────────────
# Запуск приложения
# ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
