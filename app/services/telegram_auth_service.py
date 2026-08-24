import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def verify_telegram_login(data, bot_token):
    if not bot_token:
        return False

    auth_date = data.get('auth_date')
    hash_value = data.get('hash')
    if not auth_date or not hash_value:
        return False

    try:
        auth_timestamp = int(auth_date)
    except ValueError:
        return False

    if auth_timestamp > time.time() + 300:
        return False
    if time.time() - auth_timestamp > 86400:
        return False

    check_data = [f'{key}={value}' for key, value in data.items() if key != 'hash']
    check_data.sort()
    data_check_string = '\n'.join(check_data)

    secret_key = hashlib.sha256(bot_token.encode('utf-8')).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_hash, hash_value)


def verify_telegram_web_app_init_data(init_data, bot_token, max_age_seconds=86400):
    """Validate Telegram Mini App initData and return its parsed fields.

    Mini App data uses a different HMAC key derivation than the legacy
    Telegram Login Widget, so it must not be passed to verify_telegram_login.
    """
    if not bot_token or not isinstance(init_data, str) or not init_data or len(init_data) > 16384:
        return None

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None

    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        return None

    data = dict(pairs)
    hash_value = data.pop('hash', '')
    auth_date = data.get('auth_date', '')
    if not hash_value or not auth_date:
        return None

    try:
        auth_timestamp = int(auth_date)
    except (TypeError, ValueError):
        return None

    now = time.time()
    if auth_timestamp > now + 300:
        return None
    if max_age_seconds and now - auth_timestamp > int(max_age_seconds):
        return None

    data_check_string = '\n'.join(f'{key}={data[key]}' for key in sorted(data))
    secret_key = hmac.new(b'WebAppData', bot_token.encode('utf-8'), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, hash_value):
        return None

    try:
        user = json.loads(data.get('user', ''))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(user, dict) or user.get('id') is None:
        return None

    try:
        user['id'] = int(user['id'])
    except (TypeError, ValueError):
        return None
    if user['id'] <= 0:
        return None

    return {
        **data,
        'hash': hash_value,
        'auth_date': auth_timestamp,
        'user': user,
    }
