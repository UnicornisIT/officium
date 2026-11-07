from datetime import datetime, timezone
from contextvars import ContextVar
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, has_app_context, has_request_context


_calendar_owner = ContextVar('calendar_owner', default=None)


def utc_now():
    """Return the current instant as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(value):
    """Treat legacy naive database values as UTC without changing column types."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def financial_today(owner=None, *, now=None):
    """Calendar date in the account's timezone, independent of the server clock."""
    owner = owner if owner is not None else _calendar_owner.get()
    user = getattr(owner, 'user', None) or owner
    if user is None and has_request_context():
        from flask_login import current_user

        user = current_user
    zone_name = getattr(user, 'timezone', None)
    if not zone_name:
        zone_name = current_app.config.get('APP_TIMEZONE', 'UTC') if has_app_context() else 'Europe/Moscow'
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        zone = timezone.utc
    return as_utc(now or utc_now()).astimezone(zone).date()


def account_calendar(function):
    """Keep nested date parsing in the same account calendar during bot updates."""

    @wraps(function)
    def wrapped(user, *args, **kwargs):
        token = _calendar_owner.set(user)
        try:
            return function(user, *args, **kwargs)
        finally:
            _calendar_owner.reset(token)

    return wrapped
