"""Daily vehicle reminders. Reserve each delivery before contacting Telegram."""

from datetime import timedelta
from hashlib import sha256

from flask import current_app
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models import User, Vehicle, VehicleNotificationLog
from app.services import (
    vehicle_service as vehicles,
    vehicle_maintenance_service as maintenance,
)
from app.services import (
    vehicle_document_service as documents,
    telegram_bot_service as telegram,
)
from app.time_utils import utc_now, as_utc
from app.utils import record_activity, setting_enabled
from app.services.personal_settings_service import delivery_preferences
from app.services.telegram_links_service import application_url, navigation_keyboard
from app.services.telegram_transport import TelegramDeliveryError
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from extensions import db

MAX_ATTEMPTS = 3
MIN_RETRY_SECONDS = 60
RESERVATION_TIMEOUT = timedelta(minutes=10)
CONFIRMED_FAILURES = frozenset(
    {
        'not_configured',
        'invalid_token',
        'blocked',
        'chat_unavailable',
        'bad_request',
        'rate_limited',
    }
)
RETRYABLE_FAILURES = frozenset({'not_configured', 'rate_limited'})


def due_notifications(user, *, day=None):
    day = day if day is not None else vehicles.today()
    base = application_url(
        '/', base_url=current_app.config.get('VEHICLE_NOTIFICATIONS_BASE_URL')
    )
    if not base:
        raise ValueError('Configure a public notification URL.')
    base = base.rstrip('/')
    result = []

    def add(kind, vehicle_id, text, path, document_id=None):
        context = str(vehicle_id)
        if document_id is not None:
            context += f'|{document_id}'
        digest = sha256(f'{kind}|{context}|{day.isoformat()}'.encode()).hexdigest()
        url = base + path
        result.append(
            dict(
                kind=kind,
                vehicle_id=vehicle_id,
                text=f'{text}\n{url}',
                url=url,
                payload_hash=digest,
            )
        )

    due = vehicles.due_vehicles(user, day=day)
    if due:
        add(
            'mileage_due',
            None,
            f'У вас {len(due)} автомобилей требуют обновления пробега. Откройте гараж:',
            '/vehicles',
        )
    rows = (
        Vehicle.query.filter_by(user_id=user.id, archived_at=None)
        .options(
            selectinload(Vehicle.readings),
            selectinload(Vehicle.maintenance_items),
            selectinload(Vehicle.documents),
            selectinload(Vehicle.defects),
        )
        .all()
    )
    for vehicle in rows:
        mileage = vehicles.current_mileage(vehicle)
        if maintenance.technical_condition(vehicle, mileage=mileage, day=day) in (
            'soon',
            'overdue',
        ):
            names = [
                item.name
                for item in vehicle.maintenance_items
                if item.is_active
                and maintenance.item_state(item, mileage, day)['status']
                in ('soon', 'overdue')
            ]
            if names:
                add(
                    'maintenance_due',
                    vehicle.id,
                    f'По автомобилю «{vehicle.name}» требуется обслуживание: {", ".join(names)}. Подробности:',
                    f'/vehicles/{vehicle.id}?tab=maintenance',
                )
    by_id = {v.id: v for v in rows}
    for doc in documents.expiring_soon(user, days=14, day=day):
        vehicle = by_id[doc.vehicle_id]
        remaining = (doc.expires_at - day).days
        add(
            'document_expiring',
            vehicle.id,
            f'У автомобиля «{vehicle.name}» истекает {documents.KINDS[doc.kind]} через {remaining} дн. Проверьте продление.',
            f'/vehicles/{vehicle.id}?tab=documents',
            doc.id,
        )
    return result


def send_to_user(user, kind, text, *, url=None):
    label = {
        'mileage_due': 'Открыть гараж',
        'maintenance_due': 'Открыть обслуживание',
        'document_expiring': 'Открыть документы',
    }[kind]
    return telegram.send_telegram_message(
        chat_id=user.telegram_id,
        text=text,
        reply_markup=navigation_keyboard(label, url),
    )


def notifications_enabled():
    return bool(current_app.config.get('VEHICLES_ENABLED')) and setting_enabled(
        'vehicle_notifications_enabled',
        default=bool(current_app.config.get('VEHICLE_NOTIFICATIONS_ENABLED')),
    )


def _reserve(user_id, notice, instant):
    identity = VehicleNotificationLog.query.filter_by(
        user_id=user_id,
        kind=notice['kind'],
        payload_hash=notice['payload_hash'],
    )
    previous = identity.with_entities(
        VehicleNotificationLog.id,
        VehicleNotificationLog.attempt_count,
        VehicleNotificationLog.status,
        VehicleNotificationLog.retry_at,
    ).first()
    if previous is not None:
        notification_id, attempt, status, retry_at = previous
        if status != 'failed' or retry_at is None or as_utc(retry_at) > instant:
            return None
        # A stale reader must not acquire a retry that another worker already used.
        changed = (
            identity.filter_by(status='failed', attempt_count=attempt)
            .filter(
                VehicleNotificationLog.retry_at <= instant,
                VehicleNotificationLog.attempt_count < MAX_ATTEMPTS,
            )
            .update(
                dict(
                    status='reserved',
                    attempt_count=attempt + 1,
                    attempted_at=instant,
                    retry_at=None,
                ),
                synchronize_session=False,
            )
        )
        db.session.commit()
        return (notification_id, attempt + 1) if changed else None

    row = VehicleNotificationLog(
        user_id=user_id,
        kind=notice['kind'],
        vehicle_id=notice['vehicle_id'],
        payload_hash=notice['payload_hash'],
        sent_at=instant,
        status='reserved',
        attempt_count=1,
        attempted_at=instant,
    )
    try:
        db.session.add(row)
        db.session.flush()
        notification_id = row.id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if identity.first() is None:
            raise
        return None
    return notification_id, 1


def _finish(
    notification_id, user, attempt, status, *, failure_kind=None, retry_at=None
):
    changed = VehicleNotificationLog.query.filter_by(
        id=notification_id,
        status='reserved',
        attempt_count=attempt,
    ).update(
        dict(status=status, delivered=status == 'accepted', retry_at=retry_at),
        synchronize_session=False,
    )
    if changed:
        description = f'attempt={attempt} status={status}'
        if failure_kind:
            description += f' failure_kind={failure_kind}'
        record_activity(
            'vehicle_notification_send',
            user,
            entity_type='vehicle_notification',
            entity_id=notification_id,
            actor_label=f'user:{user.id}',
            description=description,
            commit=False,
        )
    db.session.commit()
    if changed and failure_kind:
        current_app.logger.warning(
            'Vehicle notification delivery: notification_id=%s %s',
            notification_id,
            description,
        )
    return bool(changed)


def _recover_reservations(instant):
    expired = (
        db.session.query(
            VehicleNotificationLog.id,
            VehicleNotificationLog.user_id,
            VehicleNotificationLog.attempt_count,
        )
        .filter_by(status='reserved')
        .filter(
            VehicleNotificationLog.attempted_at <= instant - RESERVATION_TIMEOUT,
        )
        .all()
    )
    # A crash may have occurred after Telegram accepted the request.
    recovered = 0
    for notification_id, user_id, attempt in expired:
        user = db.session.get(User, user_id)
        if user is not None:
            recovered += _finish(
                notification_id,
                user,
                attempt,
                'unknown',
                failure_kind='reservation_expired',
            )
    return recovered


def _deliver(user, notice, *, now):
    reservation = _reserve(user.id, notice, as_utc(now or utc_now()))
    if reservation is None:
        return 'skipped'
    notification_id, attempt = reservation
    outcome, failure_kind, retry_after = 'accepted', None, None
    try:
        if not send_to_user(user, notice['kind'], notice['text'], url=notice['url']):
            outcome, failure_kind = 'failed', 'not_configured'
    except TelegramDeliveryError as exc:
        failure_kind = exc.result.failure_kind
        outcome = 'failed' if failure_kind in CONFIRMED_FAILURES else 'unknown'
        retry_after = exc.result.retry_after
    except Exception:
        outcome, failure_kind = 'unknown', 'unexpected'

    retry_at = None
    if (
        outcome == 'failed'
        and failure_kind in RETRYABLE_FAILURES
        and attempt < MAX_ATTEMPTS
    ):
        retry_at = as_utc(now or utc_now()) + timedelta(
            seconds=max(MIN_RETRY_SECONDS, retry_after or 0)
        )
    recorded = _finish(
        notification_id,
        user,
        attempt,
        outcome,
        failure_kind=failure_kind,
        retry_at=retry_at,
    )
    return 'sent' if recorded and outcome == 'accepted' else 'failed'


def send_due_notifications(now=None):
    stats = dict(sent=0, skipped=0, failed=0)
    if not notifications_enabled():
        return stats
    instant = as_utc(now or utc_now())
    stats['failed'] += _recover_reservations(instant)
    users = User.query.filter(
        User.telegram_id.isnot(None),
        User.telegram_id > 0,
        User.vehicle_notifications_enabled.is_(True),
        User.is_blocked.is_(False),
    ).all()
    for user in users:
        if getattr(user, 'is_local_test_user', False):
            continue
        zone, clock = delivery_preferences(user)
        try:
            local = instant.astimezone(ZoneInfo(zone))
            hour, minute = map(int, clock.split(':'))
        except (ZoneInfoNotFoundError, ValueError):
            current_app.logger.warning(
                'Invalid notification schedule: user_id=%s', user.id
            )
            stats['skipped'] += 1
            continue
        # Convert from a real UTC instant, never construct a potentially imaginary
        # local datetime. A skipped DST time is delivered at the first later tick;
        # a repeated local time has the same daily hash and cannot send twice.
        if local.hour * 60 + local.minute < hour * 60 + minute:
            continue
        day = local.date()
        for notice in due_notifications(user, day=day):
            stats[_deliver(user, notice, now=now)] += 1
    return stats
