"""Receive commands on hosts without inbound access from Telegram."""
from threading import Event

from flask import current_app

from app.services.telegram_bot_service import deliver_telegram_result, handle_telegram_update_once
from app.services.telegram_transport import TelegramDeliveryError, api_request, require_accepted
from extensions import db


def run_polling(*, remove_webhook=False, stop=None):
    if not current_app.config.get('TELEGRAM_BOT_TOKEN'):
        raise ValueError('TELEGRAM_BOT_TOKEN is required.')
    stop = stop if stop is not None else Event()
    status, info = api_request('getWebhookInfo', {})
    require_accepted(status)
    if not isinstance(info, dict):
        raise ValueError('Invalid webhook information.')
    if info.get('url'):
        if not remove_webhook:
            raise ValueError('Webhook is registered. Use --remove-webhook to switch to polling.')
        status, result = api_request('deleteWebhook', {'drop_pending_updates': False})
        require_accepted(status)
        if result is not True:
            raise ValueError('Webhook removal was not confirmed.')

    offset = None
    current_app.logger.info('Telegram polling started.')
    while not stop.is_set():
        payload = {'timeout': 25, 'allowed_updates': ['message', 'callback_query']}
        if offset is not None:
            payload['offset'] = offset
        status, updates = api_request('getUpdates', payload, timeout=(10, 35))
        if not status.accepted:
            if status.failure_kind in ('invalid_token', 'not_configured', 'conflict', 'blocked'):
                raise TelegramDeliveryError(status)
            stop.wait(max(5, status.retry_after or 0))
            continue
        if not isinstance(updates, list) or any(
            not isinstance(update, dict)
            or type(update.get('update_id')) is not int
            or update['update_id'] < 0
            for update in updates
        ):
            raise ValueError('Invalid polling response.')
        for update in sorted(updates, key=lambda item: item['update_id']):
            if stop.is_set():
                break
            if offset is not None and update['update_id'] < offset:
                continue
            try:
                result = handle_telegram_update_once(update)
            except Exception:
                db.session.rollback()
                current_app.logger.warning('Telegram polling update processing failed.')
                stop.wait(5)
                break
            else:
                # Preserve webhook delivery semantics: uncertain sends are not repeated.
                deliver_telegram_result(result)
                offset = update['update_id'] + 1
            finally:
                db.session.remove()
    current_app.logger.info('Telegram polling stopped.')
