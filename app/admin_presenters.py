"""Human-readable presentation helpers for the administrative interface.

Database column names and persisted enum/event values intentionally remain
unchanged.  This module is the single translation layer used by Jinja views.
"""

FIELD_LABELS = {
    'id': 'Внутренний идентификатор',
    'telegram_id': 'Идентификатор Telegram',
    'username': 'Имя пользователя',
    'first_name': 'Имя',
    'last_name': 'Фамилия',
    'email': 'Электронная почта',
    'role': 'Роль пользователя',
    'is_active': 'Статус аккаунта',
    'is_blocked': 'Статус аккаунта',
    'created_at': 'Дата регистрации',
    'auth_date': 'Последний вход',
    'last_login': 'Последний вход',
    'login_count': 'Количество входов',
    'last_login_ip': 'Последний IP-адрес',
    'last_ip': 'Последний IP-адрес',
    'last_user_agent': 'Устройство и браузер',
    'user_agent': 'Устройство и браузер',
    'action': 'Действие',
    'description': 'Описание',
    'entity_type': 'Тип объекта',
    'entity_id': 'Идентификатор объекта',
    'ip_address': 'IP-адрес',
    'debt_id': 'Идентификатор долга',
    'user_id': 'Идентификатор пользователя',
    'bank_name': 'Банк',
    'product_name': 'Финансовый продукт',
    'debt_type': 'Тип долга',
    'total_amount': 'Первоначальная сумма',
    'remaining_amount': 'Остаток долга',
    'remaining_after_payment': 'Остаток после платежа',
    'next_payment_date': 'Дата следующего платежа',
    'status': 'Статус',
    'payment_date': 'Дата платежа',
    'amount': 'Сумма',
}

ROLE_LABELS = {
    'user': 'Пользователь',
    'admin': 'Администратор',
    'superadmin': 'Супер-администратор',
}

STATUS_LABELS = {
    'active': 'Активен',
    'blocked': 'Заблокирован',
    'archived': 'В архиве',
    'enabled': 'Включено',
    'disabled': 'Выключено',
    'success': 'Успешно',
    'failed': 'Ошибка',
    'warning': 'Требует внимания',
    'idle': 'Ожидание',
    'queued': 'В очереди',
    'running': 'Выполняется',
    'healthy': 'Работает',
}

ENTITY_LABELS = {
    'user': 'Пользователь',
    'debt': 'Долг',
    'payment': 'Платёж',
    'expense': 'Расход',
    'income': 'Доход',
    'server_update': 'Обновление сервера',
    'setting': 'Настройка',
    'dictionary': 'Справочник',
}

EVENT_LABELS = {
    'account_blocked': 'Пользователь заблокирован',
    'account_unblocked': 'Пользователь разблокирован',
    'user_role_changed': 'Изменена роль пользователя',
    'user_deleted': 'Пользователь удалён',
    'server_update_started': 'Запущено обновление сервера',
    'Заблокировал пользователя': 'Пользователь заблокирован',
    'Разблокировал пользователя': 'Пользователь разблокирован',
    'Назначил администратора': 'Назначен администратор',
    'Назначил супер-администратора': 'Назначен супер-администратор',
    'Снял административные права': 'Сняты административные права',
    'Удалил пользователя': 'Пользователь удалён',
    'Начал impersonate пользователя': 'Открыт интерфейс от имени пользователя',
    'Начал impersonate тестового пользователя': 'Открыт тестовый пользователь',
    'Изменил настройки приложения': 'Изменены настройки приложения',
    'Добавил элемент справочника': 'Добавлен элемент справочника',
    'Удалил элемент справочника': 'Удалён элемент справочника',
    'Запустил обновление сервера': 'Запущено обновление сервера',
}


def _unknown(kind, value):
    text = str(value or '').strip()
    return f'Неизвестное {kind}: {text}' if text else 'Не указано'


def admin_field_label(value):
    key = str(value or '').strip()
    return FIELD_LABELS.get(key, _unknown('поле', key))


def admin_role_label(value):
    key = str(value or '').strip()
    return ROLE_LABELS.get(key, _unknown('значение роли', key))


def admin_status_label(value):
    key = str(value or '').strip()
    return STATUS_LABELS.get(key, _unknown('значение статуса', key))


def admin_entity_label(value):
    key = str(value or '').strip()
    return ENTITY_LABELS.get(key, _unknown('тип объекта', key))


def admin_event_label(value):
    key = str(value or '').strip()
    if key in EVENT_LABELS:
        return EVENT_LABELS[key]
    if ' ' in key and not key.isascii():
        return key
    return _unknown('событие', key)


def admin_event_tone(value):
    text = str(value or '').casefold()
    if any(part in text for part in ('ошиб', 'удал', 'заблок', 'failed')):
        return 'danger'
    if any(part in text for part in ('разблок', 'добав', 'назнач', 'success')):
        return 'success'
    if any(part in text for part in ('измен', 'обновлен', 'update', 'impersonate')):
        return 'info'
    return 'neutral'


def admin_event_icon(value):
    text = str(value or '').casefold()
    if 'пользовател' in text or 'account' in text or 'role' in text:
        return 'users'
    if 'обновлен' in text or 'update' in text:
        return 'update'
    if 'настрой' in text:
        return 'settings'
    if 'справочник' in text:
        return 'book'
    if 'удал' in text or 'ошиб' in text or 'failed' in text:
        return 'alert'
    return 'activity'
