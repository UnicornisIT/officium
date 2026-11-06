"""Общая проверка конфигурации приёма команд без обращения к Telegram."""

from urllib.parse import urlsplit


def receiver_configuration(config):
    mode = config.get('TELEGRAM_RECEIVE_MODE', 'webhook')
    if mode not in ('webhook', 'polling'):
        return {
            'receive_mode': 'invalid',
            'configured': False,
            'error': 'invalid_receive_mode',
        }
    result = {'receive_mode': mode, 'configured': False, 'error': None}
    if not str(config.get('TELEGRAM_BOT_TOKEN') or '').strip():
        result['error'] = 'missing_token'
        return result
    if mode == 'webhook':
        url = str(config.get('OFFICIUM_PUBLIC_URL') or '').strip().rstrip('/')
        try:
            parsed = urlsplit(url)
            valid_url = bool(
                parsed.scheme == 'https'
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            )
            # Некорректный порт выявляется только при чтении этого свойства.
            parsed.port
        except ValueError:
            valid_url = False
        if not valid_url:
            result['error'] = 'invalid_public_url'
            return result
        if not str(config.get('TELEGRAM_WEBHOOK_SECRET') or '').strip():
            result['error'] = 'missing_webhook_secret'
            return result
    result['configured'] = True
    return result
