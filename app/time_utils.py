from datetime import datetime, timezone


def utc_now():
    """Return the current instant as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(value):
    """Treat legacy naive database values as UTC without changing column types."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
