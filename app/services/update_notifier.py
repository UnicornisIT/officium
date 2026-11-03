"""Release notifications with durable reservations for each recipient."""
import re
from datetime import datetime, timedelta
from html import escape
from uuid import uuid4

import requests
from flask import current_app
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models import AppSetting, UpdateNotificationDelivery, User
from app.services.server_update_service import (
    ServerUpdateError, fetch_latest_release, read_update_status, validate_release_tag,
)
from app.services.telegram_bot_service import send_telegram_message
from app.services.telegram_transport import TelegramDeliveryError, delivery_error_message
from app.time_utils import as_utc, utc_now
from app.utils import set_setting
from extensions import db


DEFAULTS = {
    'update_notifications_enabled': 'false',
    'update_notify_new_release': 'true',
    'update_notify_install_result': 'true',
    'update_notify_recipients': 'superadmin',
    'update_check_interval_minutes': '60',
    'update_last_notified_release': '',
    'update_last_check_at': '',
    'update_last_check_status': 'not_checked',
}
EDITABLE = (
    'update_notifications_enabled', 'update_notify_new_release', 'update_notify_install_result',
    'update_notify_recipients', 'update_check_interval_minutes',
)
BOOLEAN_KEYS = EDITABLE[:3]
STATUS_LABELS = {
    'not_checked': 'Не проверялось', 'disabled': 'Уведомления выключены',
    'interval': 'Интервал проверки ещё не истёк', 'busy': 'Проверка уже выполняется',
    'missing_token': 'Токен бота не настроен', 'github_error': 'Не удалось проверить GitHub',
    'no_recipients': 'Нет подходящих получателей', 'sent': 'Уведомление отправлено',
    'no_change': 'Новых уведомлений нет', 'telegram_failed': 'Telegram отклонил отправку',
    'delivery_unknown': 'Доставка не подтверждена; автоматический повтор остановлен',
    'storage_error': 'Хранилище уведомлений недоступно',
}


def load_settings():
    values = dict(DEFAULTS)
    rows = AppSetting.query.filter(AppSetting.key.in_(DEFAULTS)).all()
    values.update({row.key: row.value for row in rows})
    return values


def validate_settings(form):
    values = {}
    for key in BOOLEAN_KEYS:
        raw = str(form.get(key, 'false')).strip().lower()
        if raw not in ('true', 'false', 'on', 'off', '1', '0'):
            raise ValueError('Некорректное значение переключателя уведомлений.')
        values[key] = 'true' if raw in ('true', 'on', '1') else 'false'
    recipients = form.get('update_notify_recipients', '')
    if recipients not in ('superadmin', 'admins'):
        raise ValueError('Выберите получателей уведомлений из списка.')
    values['update_notify_recipients'] = recipients
    raw_interval = str(form.get('update_check_interval_minutes', '')).strip()
    if not re.fullmatch(r'[0-9]{1,4}', raw_interval) or not 15 <= int(raw_interval) <= 1440:
        raise ValueError('Интервал проверки должен быть целым числом от 15 до 1440 минут.')
    values['update_check_interval_minutes'] = str(int(raw_interval))
    if values['update_notifications_enabled'] == 'true' and not current_app.config.get('TELEGRAM_BOT_TOKEN'):
        raise ValueError('Нельзя включить уведомления: токен Telegram-бота не настроен.')
    return values


def _recipients(settings):
    roles = ('admin', 'superadmin') if settings['update_notify_recipients'] == 'admins' else ('superadmin',)
    # Configured administrator IDs become superadmins on verified Telegram login.
    # Only persisted accounts may receive messages, never arbitrary raw config IDs.
    return User.query.filter(User.role.in_(roles), User.is_blocked.is_(False), User.telegram_id > 0).order_by(User.id).all()


def _instant(value):
    try:
        return as_utc(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError):
        return None


def _acquire(key, now, seconds=600):
    previous = AppSetting.query.filter_by(key=key).first()
    lease = f'{(now + timedelta(seconds=seconds)).isoformat()}|{uuid4().hex}'
    if previous is None:
        db.session.add(AppSetting(key=key, value=lease))
        try:
            db.session.commit()
            return lease
        except IntegrityError:
            db.session.rollback()
            return None
    old_value = previous.value
    expires = _instant(str(old_value or '').split('|', 1)[0])
    if expires and expires > now:
        return None
    changed = AppSetting.query.filter_by(key=key, value=old_value).update(
        {AppSetting.value: lease}, synchronize_session=False)
    db.session.commit()
    return lease if changed else None


def _release(key, lease):
    # Keep the unique row: deleting it allows a stale reader to race a new lease.
    AppSetting.query.filter_by(key=key, value=lease).update(
        {AppSetting.value: ''}, synchronize_session=False)
    db.session.commit()


def _deliver(event_key, recipients, text, *, markup=None, now):
    outcomes = []
    for user in recipients:
        row = UpdateNotificationDelivery.query.filter_by(event_key=event_key, user_id=user.id).first()
        if row:
            if row.status != 'failed':
                outcomes.append('accepted' if row.status == 'accepted' else 'unknown')
                continue
            # A rejected request may be retried; accepted/uncertain requests may not.
            changed = UpdateNotificationDelivery.query.filter_by(id=row.id, status='failed').update(
                {UpdateNotificationDelivery.status: 'reserved', UpdateNotificationDelivery.attempted_at: now},
                synchronize_session=False)
            db.session.commit()
            if not changed:
                outcomes.append('unknown')
                continue
        else:
            row = UpdateNotificationDelivery(event_key=event_key, user_id=user.id,
                status='reserved', attempted_at=now)
            db.session.add(row)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                outcomes.append('unknown')
                continue
        failure_kind = None
        try:
            accepted = send_telegram_message(user.telegram_id, text, reply_markup=markup, parse_mode='HTML')
            outcome = 'accepted' if accepted else 'failed'
            failure_kind = None if accepted else 'not_configured'
        except TelegramDeliveryError as exc:
            failure_kind = exc.result.failure_kind
            outcome = 'failed' if failure_kind in (
                'invalid_token', 'blocked', 'chat_unavailable', 'bad_request', 'rate_limited', 'not_configured',
            ) else 'unknown'
        except Exception:
            outcome, failure_kind = 'unknown', 'unexpected'
        row.status, row.failure_kind = outcome, failure_kind
        db.session.commit()
        if outcome != 'accepted':
            current_app.logger.warning('Update notification delivery: status=%s kind=%s delivery_id=%s',
                                       outcome, failure_kind, row.id)
        outcomes.append(outcome)
    if not outcomes:
        return 'no_recipients'
    if 'unknown' in outcomes:
        return 'delivery_unknown'
    return 'telegram_failed' if 'failed' in outcomes else 'sent'


def _installation_event():
    status = read_update_status(current_app.config)
    if not status or status.get('state') not in ('success', 'failed'):
        return None
    operation = str(status.get('operation_id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', operation):
        return None
    try:
        tag = validate_release_tag(status.get('tag'))
    except ServerUpdateError:
        return None
    text = f'✅ Сервер обновлён до {escape(tag)}' if status['state'] == 'success' else f'❌ Обновление до {escape(tag)} не удалось'
    return f'install:{operation}', text


def _save_result(result):
    install = result.get('installation')
    if install in ('telegram_failed', 'delivery_unknown', 'no_recipients') and result['status'] in (
            'sent', 'no_change', 'interval'):
        result['status'] = install
    set_setting('update_last_check_status', result['status'])


def _check(*, force, dry_run, now):
    settings = load_settings()
    if settings['update_notifications_enabled'] != 'true':
        return {'status': 'disabled'}
    if not current_app.config.get('TELEGRAM_BOT_TOKEN'):
        if not dry_run:
            set_setting('update_last_check_status', 'missing_token')
        return {'status': 'missing_token'}
    recipients = _recipients(settings)
    result = {'status': 'no_change', 'recipients': len(recipients)}
    # Observe terminal results independently of GitHub's interval, outside the updater.
    event = _installation_event() if settings['update_notify_install_result'] == 'true' else None
    if event:
        if dry_run:
            result['installation'] = 'would_check'
        else:
            result['installation'] = _deliver(event[0], recipients, event[1], now=now)
    last_check = _instant(settings['update_last_check_at'])
    try:
        interval = max(15, min(int(settings['update_check_interval_minutes']), 1440))
    except (TypeError, ValueError):
        interval = 60
    if not force and last_check and now < last_check + timedelta(minutes=interval):
        result['status'] = 'interval'
        if event and not dry_run:
            _save_result(result)
        return result
    if not dry_run:
        set_setting('update_last_check_at', now.isoformat(), commit=False)
        db.session.commit()
    try:
        config = dict(current_app.config, SERVER_UPDATE_REPOSITORY='UnicornisIT/officium')
        release = fetch_latest_release(config)
    except (ServerUpdateError, requests.RequestException):
        current_app.logger.warning('Update notification release check failed: kind=github_error')
        result['status'] = 'github_error'
    else:
        result['tag'] = release['tag']
        if (settings['update_notify_new_release'] == 'true'
                and release['tag'] != settings['update_last_notified_release']):
            if dry_run:
                result['status'] = 'would_send' if recipients else 'no_recipients'
            else:
                text = f'Новый релиз officium: <b>{escape(release["tag"])}</b>\n{escape(release["name"])}'
                markup = {'inline_keyboard': [[{'text': 'Открыть релиз', 'url': release['url']}]]}
                result['status'] = _deliver(f'release:{release["tag"]}', recipients, text, markup=markup, now=now)
                if result['status'] == 'sent':
                    set_setting('update_last_notified_release', release['tag'], commit=False)
    if not dry_run:
        _save_result(result)
    return result


def notify_updates(*, force=False, dry_run=False, now=None):
    instant = as_utc(now or utc_now())
    lease = None
    key = 'update_notifications_lease'
    try:
        if dry_run:
            return _check(force=force, dry_run=True, now=instant)
        if load_settings()['update_notifications_enabled'] != 'true':
            return {'status': 'disabled'}
        lease = _acquire(key, instant)
        if not lease:
            return {'status': 'busy'}
        return _check(force=force, dry_run=False, now=instant)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning('Update notification storage unavailable.')
        return {'status': 'storage_error'}
    finally:
        if lease:
            try:
                _release(key, lease)
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.warning('Update notification lease release failed.')


def send_test(user):
    if not isinstance(user, User) or user.is_blocked or user.telegram_id <= 0:
        return False, 'Текущий аккаунт не связан с доступным Telegram-чатом.'
    if not current_app.config.get('TELEGRAM_BOT_TOKEN'):
        return False, 'Токен Telegram-бота не настроен.'
    if not _acquire(f'update_notification_test:{user.id}', as_utc(utc_now()), seconds=60):
        return False, 'Повторная проверка доступна через минуту.'
    try:
        if send_telegram_message(user.telegram_id, 'Проверка уведомлений об обновлениях officium.'):
            return True, 'Telegram принял тестовое сообщение. Проверьте чат с ботом.'
    except TelegramDeliveryError as exc:
        return False, delivery_error_message(exc.result)
    except Exception:
        current_app.logger.warning('Update notification test failed.')
    return False, 'Не удалось подтвердить отправку тестового сообщения.'
