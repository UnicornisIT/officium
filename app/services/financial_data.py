from contextlib import contextmanager

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from extensions import db


class FinancialDataUnavailable(RuntimeError):
    code = 'data_unavailable'
    message = 'Финансовые данные временно недоступны. Повторите попытку позже.'


@contextmanager
def require_financial_data():
    try:
        yield
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.warning(
            'Financial data query failed (%s).', type(exc).__name__
        )
        raise FinancialDataUnavailable(FinancialDataUnavailable.message) from None
