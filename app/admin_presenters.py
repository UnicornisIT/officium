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
    'user_id': 'Идентификатор пользователя',
    'status': 'Статус',
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
    'checking': 'Проверка обновления',
    'preparing': 'Подготовка',
    'backing_up': 'Резервная копия',
    'installing': 'Установка',
    'dependencies': 'Зависимости',
    'migrating': 'Миграции',
    'restarting': 'Перезапуск',
    'verifying': 'Проверка работоспособности',
}

ENTITY_LABELS = {
    'vehicle': 'Автомобиль',
    'registration_country': 'Страна учёта',
    'user': 'Пользователь',
    'debt': 'Долг',
    'payment': 'Платёж',
    'expense': 'Расход',
    'income': 'Доход',
    'goal_transaction': 'Операция цели',
    'emergency_fund_transaction': 'Операция подушки',
    'server_update': 'Обновление сервера',
    'setting': 'Настройка',
    'dictionary': 'Справочник',
    'impersonation': 'Вход от имени пользователя',
    'export': 'Аудит-выгрузка',
    'auth': 'Вход в систему',
    'contact': 'Контакт',
    'bank': 'Справочник: банки',
    'comment_template': 'Справочник: шаблоны комментариев',
    'interest_rate': 'Справочник: процентные ставки',
    'product_type': 'Справочник: типы продуктов',
}

EVENT_LABELS = {
    'vehicle_create': 'Добавлен автомобиль',
    'vehicle_update': 'Изменён автомобиль',
    'vehicle_archive': 'Автомобиль перенесён в архив',
    'vehicle_restore': 'Автомобиль восстановлен',
    'vehicle_mileage': 'Добавлен замер пробега',
    'vehicle_snooze': 'Напоминание о пробеге отложено',
    'registration_country_save': 'Изменена страна учёта',
    'expense_create': 'Добавлен расход',
    'expense_update': 'Изменён расход',
    'expense_delete': 'Удалён расход',
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
    'Завершил impersonate': 'Завершён вход от имени пользователя',
    'Действие от имени пользователя': 'Изменение от имени пользователя',
    'Выгрузил данные аудита': 'Выгружены данные аудита',
    'Аварийный вход администратора': 'Аварийный вход администратора',
    'Неудачный аварийный вход': 'Неудачная попытка аварийного входа',
    'Изменил настройки приложения': 'Изменены настройки приложения',
    'Добавил элемент справочника': 'Добавлен элемент справочника',
    'Удалил элемент справочника': 'Удалён элемент справочника',
    'Изменил элемент справочника': 'Изменён элемент справочника',
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
    if any(part in text for part in ('ошиб', 'удал', 'заблок', 'failed', 'неудач')):
        return 'danger'
    if any(part in text for part in ('разблок', 'добав', 'назнач', 'success')):
        return 'success'
    if any(part in text for part in ('измен', 'обновлен', 'update', 'impersonate', 'от имени', 'выгруз')):
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
