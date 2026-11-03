"""Bot API boundary. Never retain response descriptions, URLs or exceptions."""
from dataclasses import dataclass
from uuid import uuid4

import requests
from flask import current_app


@dataclass(frozen=True)
class TelegramDeliveryResult:
    accepted: bool = False
    error_code: int | None = None
    failure_kind: str | None = None
    retry_after: int | None = None
    message_id: int | None = None
    correlation_id: str = ''


class TelegramDeliveryError(Exception):
    def __init__(self, result):
        self.result = result
        super().__init__(f'Telegram delivery failed: {result.failure_kind}; correlation_id={result.correlation_id}')


def _integer(value):
    return value if type(value) is int and 0 <= value <= 2147483647 else None


def api_request(method, payload, bot_token=None):
    """Return sanitized status and successful API data to trusted internal callers.

    No retries: a timeout may occur after Telegram accepted a message.
    """
    correlation_id = uuid4().hex
    token = bot_token if bot_token is not None else current_app.config.get('TELEGRAM_BOT_TOKEN', '')

    def failed(kind, code=None, retry=None):
        result = TelegramDeliveryResult(error_code=code, failure_kind=kind,
            retry_after=retry, correlation_id=correlation_id)
        current_app.logger.warning('Telegram API failure: kind=%s code=%s correlation_id=%s',
                                   kind, code, correlation_id)
        return result, None

    if not token:
        return failed('not_configured')
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/{method}',
                                 json=payload, timeout=10)
    except requests.Timeout:
        return failed('timeout')
    except requests.exceptions.SSLError:
        return failed('tls')
    except requests.ConnectionError:
        return failed('connection')
    except requests.RequestException:
        return failed('network')
    status = response.status_code
    try:
        data = response.json()
    except ValueError:
        data = None
    valid = isinstance(data, dict)
    code = _integer(data.get('error_code')) if valid else None
    if isinstance(status, int) and status >= 400:
        code = status
    if code in (401, 404):
        return failed('invalid_token', code)
    if code == 403:
        return failed('blocked', code)
    if code == 429:
        parameters = data.get('parameters') if valid else None
        retry = _integer(parameters.get('retry_after')) if isinstance(parameters, dict) else None
        return failed('rate_limited', code, retry)
    if code is not None and code >= 500:
        return failed('telegram_unavailable', code)
    if code == 400:
        description = str(data.get('description', '')).lower() if valid else ''
        kind = 'chat_unavailable' if any(text in description for text in (
            'chat not found', 'user not found', 'chat_id is empty', 'initiate conversation')) else 'bad_request'
        return failed(kind, code)
    if (code is not None or not isinstance(status, int) or not 200 <= status < 300
            or not valid or data.get('ok') is not True):
        return failed('invalid_response', code)
    result = data.get('result')
    message_id = _integer(result.get('message_id')) if isinstance(result, dict) else None
    return TelegramDeliveryResult(accepted=True, message_id=message_id,
                                  correlation_id=correlation_id), result


def require_accepted(result):
    if not result.accepted:
        raise TelegramDeliveryError(result) from None
    return True


def delivery_error_message(result):
    kind = result.failure_kind
    if kind == 'chat_unavailable':
        return 'Откройте бота и нажмите «Запустить». Проверьте, что это тот же бот, через которого выполнен вход.'
    if kind == 'blocked':
        return 'Бот заблокирован или доступ к чату запрещён. Разблокируйте бота и нажмите «Запустить».'
    if kind in ('invalid_token', 'not_configured', 'bad_request'):
        return 'Бот недоступен: обратитесь к администратору.'
    if kind == 'rate_limited':
        return f'Telegram ограничил частоту запросов. Попытка возможна через {max(60, result.retry_after or 60)} секунд.'
    if kind in ('timeout', 'connection', 'tls', 'network'):
        return 'Не удалось подтвердить доставку: ошибка сети. Проверьте чат перед новой попыткой.'
    return 'Telegram временно недоступен. Повторите проверку позже.'
