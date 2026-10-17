"""Let Telegram Web (web.telegram.org/k, /a) show the Mini App in its iframe.

Telegram's native clients open the Mini App in a top-level WebView, but
Telegram Web loads it in a cross-site iframe.  Two global defaults break that
context: ``X-Frame-Options: SAMEORIGIN`` makes the browser refuse to render
the frame, and a ``SameSite=Lax`` session cookie is neither stored nor sent
inside it, so Flask-WTF rejects the login POST for a missing CSRF session.

The relaxation is confined to the framed Mini App:

* the framed context starts only with a cross-site ``iframe`` navigation to
  ``/telegram-app`` (``Sec-Fetch-*`` headers are set by the browser and cannot
  be forged by a web page) and then continues only while its own cookie is
  present;
* that context uses a separate session cookie, ``SameSite=None; Secure;
  Partitioned``, kept in the browser's ``web.telegram.org`` partition; the
  regular ``session`` cookie and its ``SameSite=Lax`` are never changed;
* framed responses replace ``X-Frame-Options`` with CSP ``frame-ancestors``
  limited to ``'self'`` and the Telegram Web origins, so other sites still
  cannot frame the application.

CSRF protection is unchanged: every POST still needs the Flask-WTF token from
the session it is sent with.
"""

from flask import has_request_context, request
from flask.sessions import SecureCookieSessionInterface

MINI_APP_PATH = '/telegram-app'
DEFAULT_FRAME_ANCESTORS = ('https://web.telegram.org',)
DEFAULT_FRAME_COOKIE_NAME = 'officium_tg_frame'
_FRAME_DESTINATIONS = frozenset({'iframe', 'frame'})


def frame_ancestors(config):
    value = config.get('TELEGRAM_WEB_FRAME_ANCESTORS', DEFAULT_FRAME_ANCESTORS)
    if isinstance(value, str):
        value = value.replace(',', ' ').split()
    return tuple(origin for origin in value if origin.startswith('https://') or origin.startswith('http://'))


def frame_cookie_name(config):
    return config.get('TELEGRAM_WEB_FRAME_COOKIE_NAME') or DEFAULT_FRAME_COOKIE_NAME


def is_telegram_frame_request(req, config):
    """Return True when the request belongs to the Mini App inside Telegram Web."""
    if not frame_ancestors(config):
        return False
    destination = req.headers.get('Sec-Fetch-Dest', '')
    # A top-level page never uses the framed session, even if a browser
    # without cookie partitioning leaks the frame cookie to it.
    if destination == 'document':
        return False
    if frame_cookie_name(config) in req.cookies:
        return True
    return (
        req.path == MINI_APP_PATH
        and destination in _FRAME_DESTINATIONS
        and req.headers.get('Sec-Fetch-Site') == 'cross-site'
    )


def apply_frame_headers(response, req, config):
    """Replace X-Frame-Options with frame-ancestors for framed Mini App responses."""
    if not is_telegram_frame_request(req, config):
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        return response
    response.headers.pop('X-Frame-Options', None)
    sources = ' '.join(("'self'",) + frame_ancestors(config))
    response.headers['Content-Security-Policy'] = f'frame-ancestors {sources}'
    return response


class TelegramFrameSessionInterface(SecureCookieSessionInterface):
    """Signed cookie session that switches to a partitioned cookie in the frame."""

    def _framed(self, app):
        return has_request_context() and is_telegram_frame_request(request, app.config)

    def get_cookie_name(self, app):
        if self._framed(app):
            return frame_cookie_name(app.config)
        return super().get_cookie_name(app)

    def get_cookie_samesite(self, app):
        if self._framed(app):
            return 'None'
        return super().get_cookie_samesite(app)

    def get_cookie_secure(self, app):
        # Browsers drop SameSite=None cookies without Secure.
        if self._framed(app):
            return True
        return super().get_cookie_secure(app)

    def get_cookie_partitioned(self, app):
        if self._framed(app):
            return True
        return super().get_cookie_partitioned(app)
