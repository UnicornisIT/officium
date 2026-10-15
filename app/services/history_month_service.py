"""Which month group of an operation history is expanded on the page."""

import re
from datetime import date


_MONTH_PATTERN = re.compile(r'^(\d{4})-(\d{2})$')


def month_key(value):
    """Return the ``YYYY-MM`` key that ``group_entries_by_month`` uses for a date."""
    return value.strftime('%Y-%m')


def parse_month_param(value):
    """Validate a ``?month=YYYY-MM`` query value; anything else is ignored."""
    match = _MONTH_PATTERN.match(str(value or '').strip())
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        return None
    return f'{year:04d}-{month:02d}'


def resolve_active_month(groups, requested=None, today=None):
    """Pick the expanded month: the requested one, then today's, then the newest."""
    available = [group['year_month'] for group in groups]
    requested_month = parse_month_param(requested)
    if requested_month in available:
        return requested_month
    current_month = month_key(today or date.today())
    if not available or current_month in available:
        return current_month
    return available[0]
