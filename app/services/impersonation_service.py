"""Administrator impersonation: start, time limit, end and audit trail."""

from datetime import timedelta

from flask import g, request, session
from flask_login import login_user, logout_user

from app.models import ImpersonationSession, User
from app.time_utils import as_utc, utc_now
from app.utils import actor_label_for, client_ip, get_setting, record_activity
from extensions import db


REASON_MIN_LENGTH = 5
REASON_MAX_LENGTH = 200
TIMEOUT_DEFAULT_MINUTES = 30
TIMEOUT_MIN_MINUTES = 5
TIMEOUT_MAX_MINUTES = 120
SESSION_KEYS = ('original_admin_id', 'impersonated_user_id', 'impersonation_session_id')

SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

START_ACTION = 'Начал impersonate пользователя'
CHANGE_ACTION = 'Действие от имени пользователя'
START_TEST_ACTION = 'Начал impersonate тестового пользователя'
END_ACTION = 'Завершил impersonate'

END_REASON_LABELS = {
    'manual': 'администратор вернулся в панель',
    'timeout': 'истекло время сеанса',
    'logout': 'выход из аккаунта',
    'blocked': 'пользователь заблокирован',
    'invalid': 'сеанс недействителен',
    'role_changed': 'пользователь получил права администратора',
}


class ImpersonationError(ValueError):
    pass


def timeout_minutes():
    try:
        value = int(str(get_setting('impersonation_timeout_minutes', TIMEOUT_DEFAULT_MINUTES)).strip())
    except (TypeError, ValueError):
        return TIMEOUT_DEFAULT_MINUTES
    return min(max(value, TIMEOUT_MIN_MINUTES), TIMEOUT_MAX_MINUTES)


def validate_reason(raw_reason):
    reason = ' '.join(str(raw_reason or '').split())
    if not REASON_MIN_LENGTH <= len(reason) <= REASON_MAX_LENGTH:
        raise ImpersonationError(
            f'Укажите причину входа от {REASON_MIN_LENGTH} до {REASON_MAX_LENGTH} символов.'
        )
    return reason


def ensure_can_impersonate(admin, target):
    if not getattr(admin, 'is_superadmin', False):
        raise ImpersonationError('Входить от имени пользователя может только супер-администратор.')
    if target.is_admin:
        # Administrators have their own personal cabinet; acting as one would mix it with the panel.
        raise ImpersonationError('Нельзя войти от имени администратора.')
    if target.is_blocked:
        raise ImpersonationError('Нельзя открыть интерфейс от имени этого пользователя.')
    if str(target.get_id()) == str(admin.get_id()):
        raise ImpersonationError('Нельзя войти от имени самого себя.')


def _naive(value):
    return value.replace(tzinfo=None) if value.tzinfo else value


def start(admin, target, *, reason=None, read_only=True, test_user=False):
    """Switch the browser session from ``admin`` to ``target`` and record it."""
    now = utc_now()
    record = ImpersonationSession(
        admin_user_id=admin.id if isinstance(admin.id, int) else None,
        actor_label=actor_label_for(admin),
        target_user_id=target.id if isinstance(target.id, int) else None,
        target_label=actor_label_for(target),
        reason=reason,
        read_only=read_only,
        started_at=_naive(now),
        expires_at=_naive(now + timedelta(minutes=timeout_minutes())),
        ip_start=client_ip(),
    )
    db.session.add(record)
    db.session.commit()

    mode = 'только просмотр' if read_only else 'изменения разрешены'
    description = f'Вход от имени {record.target_label}; режим: {mode}'
    if reason:
        description += f'; причина: {reason}'
    record_activity(
        START_TEST_ACTION if test_user else START_ACTION,
        admin,
        entity_type='user',
        entity_id=record.target_user_id,
        description=description,
        ip_address=client_ip(),
        user_agent=request.headers.get('User-Agent'),
        impersonation_session_id=record.id,
    )

    original_admin_id = admin.get_id()
    logout_user()
    login_user(target)
    session['original_admin_id'] = original_admin_id
    session['impersonated_user_id'] = str(target.get_id())
    session['impersonation_session_id'] = record.id
    return record


def current_record():
    """The active ImpersonationSession for this browser session, if any."""
    if 'impersonation_record' in g:
        return g.impersonation_record
    record_id = session.get('impersonation_session_id')
    record = db.session.get(ImpersonationSession, record_id) if record_id else None
    g.impersonation_record = record
    return record


def minutes_left(record):
    remaining = as_utc(record.expires_at) - utc_now()
    return max(int(remaining.total_seconds() // 60) + (1 if remaining.total_seconds() % 60 else 0), 0)


def is_expired(record):
    return as_utc(record.expires_at) <= utc_now()


def clear_session():
    for key in SESSION_KEYS:
        session.pop(key, None)
    g.pop('impersonation_record', None)


def _original_admin(original_admin_id, emergency_enabled):
    from app.routes.auth import AdminUser

    if str(original_admin_id) == 'admin':
        return AdminUser() if emergency_enabled else None
    try:
        admin = db.session.get(User, int(original_admin_id))
    except (TypeError, ValueError):
        return None
    if not admin or not admin.is_superadmin or admin.is_blocked:
        return None
    return admin


def finish(end_reason, *, restore, emergency_enabled):
    """Close the active session. Returns the restored admin, or None."""
    original_admin_id = session.get('original_admin_id')
    record = current_record()
    clear_session()

    admin = _original_admin(original_admin_id, emergency_enabled) if original_admin_id else None
    if record and record.ended_at is None:
        record.ended_at = _naive(utc_now())
        record.end_reason = end_reason
        record.ip_end = client_ip()
        db.session.commit()
        record_activity(
            END_ACTION,
            admin,
            entity_type='user',
            entity_id=record.target_user_id,
            description=(
                f'Сеанс от имени {record.target_label} завершён: '
                f'{END_REASON_LABELS.get(end_reason, end_reason)}'
            ),
            ip_address=client_ip(),
            user_agent=request.headers.get('User-Agent'),
            impersonation_session_id=record.id,
            # Keeps attribution even when the admin account cannot be restored.
            actor_label=record.actor_label,
        )

    logout_user()
    if restore and admin is not None:
        login_user(admin)
        return admin
    return None


def record_change(record):
    """Log a successful change request made while acting as the user.

    Only the method and route are stored: request bodies may contain amounts.
    """
    admin = db.session.get(User, record.admin_user_id) if record.admin_user_id else None
    record_activity(
        CHANGE_ACTION,
        admin,
        entity_type='impersonation',
        entity_id=record.target_user_id,
        description=f'{request.method} {request.path} от имени {record.target_label}',
        ip_address=client_ip(),
        user_agent=request.headers.get('User-Agent'),
        impersonation_session_id=record.id,
        actor_label=record.actor_label,
    )


def _active_filter():
    return (
        ImpersonationSession.ended_at.is_(None),
        ImpersonationSession.expires_at > _naive(utc_now()),
    )


def active_count():
    return ImpersonationSession.query.filter(*_active_filter()).count()


def active_target_ids_query():
    """Subquery of user ids that an administrator is acting as right now."""
    return db.session.query(ImpersonationSession.target_user_id).filter(
        *_active_filter(), ImpersonationSession.target_user_id.isnot(None),
    )
