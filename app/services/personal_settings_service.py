"""Owner-only personal settings and an atomically rate-limited Telegram test."""
import re
from functools import lru_cache
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from flask import abort, current_app
from sqlalchemy import or_

from app.models import User
from app.services import telegram_bot_service as telegram
from app.time_utils import as_utc, utc_now
from extensions import db

TEST_COOLDOWN_SECONDS = 60


def persistent_user(user):
    return isinstance(user, User) and not getattr(user, 'is_local_test_user', False)


def has_telegram(user):
    return persistent_user(user) and bool(user.telegram_id and user.telegram_id > 0)


def delivery_preferences(user):
    # NULL retains the installation's previous timezone and delivery hour.
    zone = getattr(user, 'notification_timezone', None) or current_app.config['APP_TIMEZONE']
    clock = getattr(user, 'notification_delivery_time', None) or f"{current_app.config.get('VEHICLE_NOTIFICATIONS_HOUR', 10):02d}:00"
    return zone, clock


@lru_cache(maxsize=1)
def timezone_choices():
    preferred = ['UTC', 'Europe/Moscow', 'Europe/Kaliningrad', 'Europe/Samara',
                 'Asia/Yekaterinburg', 'Asia/Omsk', 'Asia/Novosibirsk', 'Asia/Krasnoyarsk',
                 'Asia/Irkutsk', 'Asia/Yakutsk', 'Asia/Vladivostok', 'Asia/Magadan', 'Asia/Kamchatka']
    zones = available_timezones() - {'localtime', 'Factory'}
    return [zone for zone in preferred if zone in zones] + sorted(zones - set(preferred))


def save(user, data):
    if not persistent_user(user):
        abort(403)
    section = data.get('section', 'notifications')
    if section == 'privacy':
        user.hide_amounts_on_open = data.get('hide_amounts_on_open') == 'on'
    elif section == 'notifications':
        if not has_telegram(user):
            abort(403)
        previous_zone, previous_clock = delivery_preferences(user)
        zone = data.get('notification_timezone', previous_zone).strip()
        clock = data.get('notification_delivery_time', previous_clock).strip()
        if zone not in timezone_choices():
            raise ValueError('Выберите часовой пояс из списка IANA.')
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Часовой пояс недоступен на сервере.') from None
        if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', clock):
            raise ValueError('Укажите время доставки в формате ЧЧ:ММ.')
        user.notification_timezone = zone
        user.notification_delivery_time = clock
        user.vehicle_notifications_enabled = data.get('vehicle_notifications_enabled') == 'on'
    else:
        raise ValueError('Неизвестная карточка настроек.')
    db.session.commit()
    return section


def bot_url():
    username = str(current_app.config.get('TELEGRAM_BOT_USERNAME') or '').strip().lstrip('@')
    return f'https://t.me/{username}' if re.fullmatch(r'[A-Za-z0-9_]{5,32}', username) else None


def test_notification(user, *, now=None):
    if not has_telegram(user) or user.is_blocked:
        abort(403)
    if not current_app.config.get('TELEGRAM_BOT_TOKEN'):
        return False, 'Отправка недоступна: Telegram-бот не настроен на сервере.', 503
    instant = as_utc(now or utc_now()).replace(tzinfo=None)
    # Reserve before contacting Telegram. A failed attempt also consumes the slot.
    changed = User.query.filter(User.id == user.id, or_(
        User.notification_test_requested_at.is_(None),
        User.notification_test_requested_at <= instant - timedelta(seconds=TEST_COOLDOWN_SECONDS),
    )).update({User.notification_test_requested_at: instant}, synchronize_session=False)
    db.session.commit()
    if not changed:
        return False, 'Повторная проверка доступна через минуту после предыдущей попытки.', 429
    try:
        delivered = bool(telegram.send_telegram_message(user.telegram_id,
            'Проверка уведомлений officium. Это тестовое сообщение по вашему запросу из профиля.'))
    except Exception:
        delivered = False
    if delivered:
        return True, 'Telegram принял тестовое сообщение. Проверьте чат с ботом.', 200
    current_app.logger.warning('Profile notification test failed: user_id=%s', user.id)
    return False, 'Не удалось отправить сообщение. Откройте бота и нажмите «Запустить». Повторите проверку через минуту.', 502
