"""Receive commands on hosts without inbound access from Telegram."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event

from flask import current_app

from app.services.telegram_bot_service import (
    _update_context,
    deliver_telegram_result,
    handle_telegram_update_once,
)
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

    app = current_app._get_current_object()
    executor = ThreadPoolExecutor(max_workers=app.config['TELEGRAM_POLLING_WORKERS'])
    current_app.logger.info('Telegram polling started.')
    try:
        _poll_updates(app, executor, stop)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        current_app.logger.info('Telegram polling stopped.')


def _poll_updates(app, executor, stop):
    offset = None
    while not stop.is_set():
        payload = {'timeout': 25, 'allowed_updates': ['message', 'callback_query']}
        if offset is not None:
            payload['offset'] = offset
        status, updates = api_request('getUpdates', payload, timeout=(10, 35))
        if stop.is_set():
            return
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
        groups = {}
        pending = {}
        for update in sorted(updates, key=lambda item: item['update_id']):
            update_id = update['update_id']
            if update_id in pending or (offset is not None and update_id < offset):
                continue
            # Conversation state is keyed by sender, including across chats.
            try:
                context = _update_context(update)
                sender = str(context['telegram_id'] or context['chat_id'])
            except AttributeError:
                sender = None  # Let the worker apply the usual processing error policy.
            groups.setdefault(sender, []).append(update)
            pending[update_id] = None

        futures = [executor.submit(_process_updates, app, group, stop) for group in groups.values()]
        completed = set()
        for future in as_completed(futures):
            if stop.is_set():
                return
            completed.update(future.result())

        # A single offset can acknowledge only the completed prefix of the batch.
        for update_id in pending:
            if update_id not in completed:
                stop.wait(5)
                break
            offset = update_id + 1


def _process_updates(app, updates, stop):
    completed = []
    for update in updates:
        if stop.is_set():
            break
        # A new app context gives each update its own SQLAlchemy session scope.
        with app.app_context():
            try:
                result = handle_telegram_update_once(update)
            except Exception:
                db.session.rollback()
                app.logger.warning('Telegram polling update processing failed.')
                break
            # The update is committed, so an uncertain send is never repeated.
            deliver_telegram_result(result)
            completed.append(update['update_id'])
    return completed
