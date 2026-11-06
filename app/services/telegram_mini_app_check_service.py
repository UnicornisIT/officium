"""Verify the default Telegram menu URL without changing bot settings."""

from hashlib import sha256
from threading import Lock
from time import monotonic

from flask import current_app

from app.services.telegram_links_service import mini_app_url
from app.services.telegram_transport import api_request


CACHE_TTL = 3600
_cache = {}
_cache_lock = Lock()


def check_mini_app_registered(bot_token, public_url):
    expected_url = mini_app_url(public_url)
    if not bot_token or not expected_url:
        return False
    key = (sha256(bot_token.encode()).digest(), expected_url)
    with _cache_lock:
        now = monotonic()
        cached = _cache.get(key)
        if cached and cached[0] > now:
            return cached[1]

        # Names and descriptions contain no registration data; only the menu
        # exposes the configured Web App URL through the Bot API.
        status, menu = api_request('getChatMenuButton', {}, bot_token, timeout=5)
        web_app = menu.get('web_app') if isinstance(menu, dict) else None
        registered = bool(
            status.accepted
            and isinstance(menu, dict)
            and menu.get('type') == 'web_app'
            and isinstance(web_app, dict)
            and web_app.get('url') == expected_url
        )
        for cached_key, (expires_at, _) in list(_cache.items()):
            if expires_at <= now:
                del _cache[cached_key]
        if len(_cache) >= 256:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (monotonic() + CACHE_TTL, registered)
        if not registered:
            current_app.logger.warning(
                'Telegram Mini App menu URL could not be verified. '
                'Configure the application URL in @BotFather: '
                'Bot Settings → Menu Button → Configure Mini App.'
            )
        return registered
