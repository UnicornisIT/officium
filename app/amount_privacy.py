"""Presentation-only markup: keep the plain currency formatter for APIs/exports."""
from flask import has_request_context, request
from flask_login import current_user
from markupsafe import Markup, escape

from app.utils import format_currency


def private_amount(value):
    return Markup('<span data-private-money><span class="money-value">{}</span>'
                  '<span class="money-mask" aria-label="Сумма скрыта">••••</span></span>').format(escape(value))


def display_money(value):
    formatted = format_currency(value)
    if (has_request_context() and not request.path.startswith('/admin') and
            getattr(current_user, 'hide_amounts_on_open', False) and '₽' in formatted):
        return private_amount(formatted)
    return formatted
