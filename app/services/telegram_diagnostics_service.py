"""Explicit operator diagnostics; read-only Bot API calls, no chat messages."""
from time import time
from urllib.parse import urlsplit

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.models import AppSetting
from app.services.telegram_transport import api_request
from app.services.vehicle_notification_service import notifications_enabled
from extensions import db


def _reserve_network_check():
    # Shared database CAS, including competing processes and process restarts.
    key = 'telegram_diagnostics_requested_at'
    now = int(time())
    row = AppSetting.query.filter_by(key=key).first()
    if row is None:
        db.session.add(AppSetting(key=key, value=str(now)))
        try:
            db.session.commit()
            return True
        except IntegrityError:
            db.session.rollback()
            return False
    previous = row.value
    try:
        if now - int(previous) < 60:
            return False
    except (TypeError, ValueError):
        pass
    changed = AppSetting.query.filter_by(key=key, value=previous).update(
        {AppSetting.value: str(now)}, synchronize_session=False)
    db.session.commit()
    return bool(changed)


def diagnose(*, network=False):
    config = current_app.config
    report = {name: bool(config.get(name)) for name in (
        'TELEGRAM_BOT_TOKEN', 'TELEGRAM_BOT_USERNAME', 'TELEGRAM_BOT_ENABLED',
        'TELEGRAM_WEBHOOK_SECRET', 'OFFICIUM_PUBLIC_URL', 'VEHICLES_ENABLED',
        'VEHICLE_NOTIFICATIONS_ENABLED')}
    report['vehicle_notifications_effective'] = notifications_enabled()
    report['network'] = 'not_requested'
    if not network:
        return report
    if not config.get('TELEGRAM_BOT_TOKEN'):
        report['network'] = 'not_configured'
        return report
    if not _reserve_network_check():
        report['network'] = 'cooldown_60_seconds'
        return report
    report['network'] = 'checked'
    for method in ('getMe', 'getWebhookInfo'):
        status, data = api_request(method, {})
        report[method] = dict(accepted=status.accepted, failure_kind=status.failure_kind,
                             error_code=status.error_code, correlation_id=status.correlation_id)
        if not status.accepted:
            continue
        if not isinstance(data, dict):
            report[method]['accepted'] = False
            report[method]['failure_kind'] = 'invalid_response'
            continue
        if method == 'getMe':
            actual = data.get('username')
            expected = str(config.get('TELEGRAM_BOT_USERNAME') or '').strip().lstrip('@')
            report[method]['username_matches'] = bool(expected and isinstance(actual, str)
                                                        and actual.casefold() == expected.casefold())
            report[method]['is_bot'] = data.get('is_bot') is True
        else:
            # Never expose URL, IP address, description or a raw Telegram error.
            url = data.get('url')
            expected = (config.get('OFFICIUM_PUBLIC_URL') or '').rstrip('/') + '/telegram/webhook'
            report[method]['registered'] = bool(url)
            report[method]['url_matches'] = bool(config.get('OFFICIUM_PUBLIC_URL') and url == expected)
            try:
                report[method]['https'] = isinstance(url, str) and urlsplit(url).scheme == 'https'
            except ValueError:
                report[method]['https'] = False
            count = data.get('pending_update_count')
            report[method]['pending_update_count'] = count if type(count) is int and count >= 0 else None
            report[method]['has_last_error'] = bool(data.get('last_error_date') or data.get('last_error_message'))
    return report
