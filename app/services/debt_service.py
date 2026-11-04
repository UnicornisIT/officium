import threading

from flask_login import current_user
from sqlalchemy import update

from app.models import Debt, User
from app.utils import get_setting, is_local_test_user
from extensions import db

_demo_debts = None
_demo_lock = threading.Lock()


def check_debt_limit(user, error_suffix=''):
    """Hold the owner's transaction lock until the caller commits or rolls back."""
    if db.session.get_bind().dialect.name == 'sqlite':
        # SQLite ignores FOR UPDATE; take its write lock before reading the quota.
        db.session.execute(update(User).where(User.id == user.id).values(id=User.id))
    else:
        User.query.filter_by(id=user.id).with_for_update().one()

    try:
        debt_limit = max(int(get_setting('debt_limit_per_user', '50')), 1)
    except (TypeError, ValueError):
        debt_limit = 50
    # A locking read sees committed inserts even under MySQL REPEATABLE READ.
    debts = Debt.query.with_entities(Debt.id).filter_by(user_id=user.id).limit(debt_limit).with_for_update().all()
    if len(debts) >= debt_limit:
        raise ValueError(f'Достигнут лимит долгов: {debt_limit}{error_suffix}')


def _load_demo_debts():
    # Callers hold _demo_lock through initialization and the complete operation.
    global _demo_debts
    if _demo_debts is None:
        from app.services.finance_summary_service import get_finance_summary

        summary = get_finance_summary(None)
        _demo_debts = [debt for debt in summary['active_debts']]
    return _demo_debts


def get_demo_debts():
    with _demo_lock:
        return list(_load_demo_debts())


def get_demo_debt(debt_id):
    with _demo_lock:
        return next((debt for debt in _load_demo_debts() if debt.id == debt_id), None)


def create_demo_debt(**kwargs):
    with _demo_lock:
        debts = _load_demo_debts()
        next_id = max((debt.id for debt in debts), default=100) + 1
        debt = Debt(id=next_id, **kwargs)
        debts.append(debt)
        return debt


def delete_demo_debt(debt_id):
    with _demo_lock:
        debts = _load_demo_debts()
        for debt in debts:
            if debt.id == debt_id:
                debts.remove(debt)
                return True
        return False


def get_user_debt(debt_id):
    if is_local_test_user():
        return get_demo_debt(debt_id)
    return Debt.query.filter_by(id=debt_id, user_id=current_user.id).first()
