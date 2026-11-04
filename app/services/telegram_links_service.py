"""Ссылки из сообщений Telegram на разделы приложения."""

from urllib.parse import urlsplit

from flask import current_app


def application_url(path, *, base_url=None):
    base = (
        str(base_url or current_app.config.get('OFFICIUM_PUBLIC_URL') or '')
        .strip()
        .rstrip('/')
    )
    try:
        parsed = urlsplit(base)
        if (
            parsed.scheme not in ('http', 'https')
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            return None
    except ValueError:
        return None
    return base + '/' + path.lstrip('/')


def navigation_keyboard(label, url):
    if not url:
        return None
    return {'inline_keyboard': [[{'text': label, 'url': url}]]}
