"""Daily vehicle reminders. Reserve each delivery before contacting Telegram."""
from hashlib import sha256
from urllib.parse import urlsplit

from flask import current_app
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models import User, Vehicle, VehicleNotificationLog
from app.services import vehicle_service as vehicles, vehicle_maintenance_service as maintenance
from app.services import vehicle_document_service as documents, telegram_bot_service as telegram
from app.time_utils import utc_now, as_utc
from app.utils import record_activity, setting_enabled
from app.services.personal_settings_service import delivery_preferences
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from extensions import db


def due_notifications(user, *, day=None):
    day = day if day is not None else vehicles.today()
    base = (current_app.config.get('VEHICLE_NOTIFICATIONS_BASE_URL') or
            current_app.config.get('OFFICIUM_PUBLIC_URL') or '').rstrip('/')
    if urlsplit(base).scheme not in ('http', 'https') or not urlsplit(base).netloc:
        raise ValueError('Configure a public notification URL.')
    result = []

    def add(kind, vehicle_id, text, document_id=None):
        context = str(vehicle_id)
        if document_id is not None:
            context += f'|{document_id}'
        digest = sha256(f'{kind}|{context}|{day.isoformat()}'.encode()).hexdigest()
        result.append(dict(kind=kind, vehicle_id=vehicle_id, text=text, payload_hash=digest))

    due = vehicles.due_vehicles(user, day=day)
    if due:
        add('mileage_due', None, f'У вас {len(due)} автомобилей требуют обновления пробега. Откройте гараж: {base}/vehicles')
    rows = Vehicle.query.filter_by(user_id=user.id, archived_at=None).options(
        selectinload(Vehicle.readings), selectinload(Vehicle.maintenance_items),
        selectinload(Vehicle.documents), selectinload(Vehicle.defects)).all()
    for vehicle in rows:
        mileage = vehicles.current_mileage(vehicle)
        if maintenance.technical_condition(vehicle, mileage=mileage, day=day) in ('soon', 'overdue'):
            names = [item.name for item in vehicle.maintenance_items if item.is_active and
                     maintenance.item_state(item, mileage, day)['status'] in ('soon', 'overdue')]
            if names:
                add('maintenance_due', vehicle.id, f'По автомобилю «{vehicle.name}» требуется обслуживание: {", ".join(names)}. Подробности: {base}/vehicles/{vehicle.id}?tab=maintenance')
    by_id = {v.id: v for v in rows}
    for doc in documents.expiring_soon(user, days=14, day=day):
        vehicle = by_id[doc.vehicle_id]
        remaining = (doc.expires_at - day).days
        add('document_expiring', vehicle.id,
            f'У автомобиля «{vehicle.name}» истекает {documents.KINDS[doc.kind]} через {remaining} дн. Проверьте продление. {base}/vehicles/{vehicle.id}?tab=documents', doc.id)
    return result


def send_to_user(user, kind, text):
    return telegram.send_telegram_message(chat_id=user.telegram_id, text=text)


def notifications_enabled():
    return bool(current_app.config.get('VEHICLES_ENABLED')) and setting_enabled(
        'vehicle_notifications_enabled', default=bool(current_app.config.get('VEHICLE_NOTIFICATIONS_ENABLED')))


def send_due_notifications(now=None):
    stats = dict(sent=0, skipped=0, failed=0)
    if not notifications_enabled():
        return stats
    instant = as_utc(now or utc_now())
    users = User.query.filter(User.telegram_id.isnot(None), User.telegram_id > 0,
                              User.vehicle_notifications_enabled.is_(True), User.is_blocked.is_(False)).all()
    for user in users:
        if getattr(user, 'is_local_test_user', False):
            continue
        zone, clock = delivery_preferences(user)
        try:
            local = instant.astimezone(ZoneInfo(zone))
            hour, minute = map(int, clock.split(':'))
        except (ZoneInfoNotFoundError, ValueError):
            current_app.logger.warning('Invalid notification schedule: user_id=%s', user.id)
            stats['skipped'] += 1
            continue
        # Convert from a real UTC instant, never construct a potentially imaginary
        # local datetime. A skipped DST time is delivered at the first later tick;
        # a repeated local time has the same daily hash and cannot send twice.
        if local.hour * 60 + local.minute < hour * 60 + minute:
            continue
        day = local.date()
        for notice in due_notifications(user, day=day):
            row = VehicleNotificationLog(user_id=user.id, kind=notice['kind'],
                vehicle_id=notice['vehicle_id'], payload_hash=notice['payload_hash'], sent_at=instant, delivered=False)
            # Commit the reservation first: concurrent runners cannot both send.
            try:
                db.session.add(row)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                stats['skipped'] += 1
                continue
            try:
                delivered = bool(send_to_user(user, notice['kind'], notice['text']))
            except Exception:
                delivered = False
            row.delivered = delivered
            if not delivered:
                current_app.logger.warning('Vehicle notification delivery failed: notification_id=%s', row.id)
            record_activity('vehicle_notification_send', user, entity_type='vehicle_notification',
                entity_id=row.id, actor_label=f'user:{user.id}', description=f'delivered={str(delivered).lower()}', commit=False)
            db.session.commit()
            stats['sent' if delivered else 'failed'] += 1
    return stats
