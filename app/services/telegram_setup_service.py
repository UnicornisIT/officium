"""Настройка получения команд при явном запуске установщика."""
from urllib.parse import urlsplit

from flask import current_app

from app.services.telegram_transport import api_request, require_accepted
from app.utils import telegram_bot_enabled


def setup(*, dry_run=False):
    config = current_app.config
    mode = config.get('TELEGRAM_RECEIVE_MODE', 'webhook')
    if mode not in ('webhook', 'polling'):
        raise ValueError('TELEGRAM_RECEIVE_MODE must be webhook or polling.')
    url = str(config.get('OFFICIUM_PUBLIC_URL') or '').rstrip('/')
    parsed = urlsplit(url)
    webhook_ready = (
        parsed.scheme == 'https'
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and bool(config.get('TELEGRAM_WEBHOOK_SECRET'))
    )
    state = {
        'bot_enabled': telegram_bot_enabled(config),
        'receiver_enabled': bool(
            config.get('TELEGRAM_BOT_TOKEN') and (mode == 'polling' or webhook_ready)
        ),
        'receive_mode': mode,
        'monthly_reminders': bool(config.get('TELEGRAM_BOT_TOKEN')),
    }
    if dry_run:
        return state
    if not config.get('TELEGRAM_BOT_TOKEN'):
        if state['bot_enabled']:
            raise ValueError('TELEGRAM_BOT_TOKEN is required.')
        return state
    if state['bot_enabled'] and mode == 'webhook':
        if not webhook_ready:
            raise ValueError('Webhook requires an HTTPS OFFICIUM_PUBLIC_URL and TELEGRAM_WEBHOOK_SECRET.')
    status, bot = api_request('getMe', {})
    require_accepted(status)
    if not isinstance(bot, dict) or bot.get('is_bot') is not True:
        raise ValueError('Telegram bot identity could not be verified.')
    expected = str(config.get('TELEGRAM_BOT_USERNAME') or '').strip().lstrip('@')
    if expected and str(bot.get('username') or '').casefold() != expected.casefold():
        raise ValueError('Telegram bot username does not match the configured token.')
    if not state['receiver_enabled']:
        return state
    if mode == 'polling':
        method, payload = 'deleteWebhook', {'drop_pending_updates': False}
    else:
        method, payload = 'setWebhook', {
            'url': url + '/telegram/webhook', 'secret_token': config['TELEGRAM_WEBHOOK_SECRET'],
            'allowed_updates': ['message', 'callback_query'], 'drop_pending_updates': False,
        }
    status, result = api_request(method, payload)
    require_accepted(status)
    if result is not True:
        raise ValueError('Telegram receive mode was not confirmed.')
    return state
