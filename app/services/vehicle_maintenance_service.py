"""Maintenance schedules and atomic, owner-scoped service history."""
import calendar
from datetime import timedelta

from flask import abort
from app.models import (ConsumableTemplate, Expense, VehicleMaintenanceItem,
                        VehicleServiceRecord, VehicleDefect, VehicleOdometerReading)
from app.services import vehicle_service as vehicles, vehicle_cost_service as costs
from app.utils import parse_date, record_activity
from extensions import db

LABELS = {'ok': 'В порядке', 'soon': 'Скоро', 'overdue': 'Просрочено', 'unknown': 'Укажите последнюю замену'}
COLORS = {'ok': 'success', 'soon': 'warning', 'overdue': 'danger', 'unknown': 'secondary'}
SEVERITIES = {'critical': 'Критическая', 'attention': 'Требует внимания', 'minor': 'Незначительная'}


def _text(data, key, limit, required=False):
    value = str(data.get(key) or '').strip()
    if len(value) > limit or (required and not value):
        label = {'name': 'Название', 'title': 'Название неисправности', 'comment': 'Комментарий'}[key]
        raise ValueError(f'Проверьте поле «{label}» (до {limit} символов).')
    return value


def _intervals(data, trailer=False):
    values = {key: vehicles._integer(data[key], 'Интервал', 1200 if key == 'interval_months' else 2147483647)
              if data.get(key) not in (None, '') else None for key in ('interval_km', 'interval_months')}
    if trailer and values['interval_km'] is not None:
        raise ValueError('Для прицепа доступны только интервалы по месяцам.')
    if not any(values.values()) or 0 in values.values():
        raise ValueError('Укажите положительный интервал в километрах или месяцах.')
    return values


def _day(value, label):
    day = parse_date(value, label, required=True)
    if day > vehicles.today():
        raise ValueError('Дата не может быть в будущем.')
    return day


def _audit(user, action, row, commit=True):
    db.session.flush()
    record_activity(action, user, entity_type=row.__tablename__, entity_id=row.id, commit=False)
    if commit:
        db.session.commit()
    return row


def save_template(user, data, template_id=None):
    vehicles.require_enabled()
    if not getattr(user, 'is_superadmin', False):
        abort(403)
    row = ConsumableTemplate.query.filter_by(id=template_id).first_or_404() if template_id else ConsumableTemplate()
    template_set = data.get('template_set')
    if template_set not in ('passenger', 'truck'):
        raise ValueError('Выберите набор шаблонов.')
    values = _intervals(data)
    row.name = _text(data, 'name', 150, True)
    row.template_set = template_set
    row.interval_km, row.interval_months = values['interval_km'], values['interval_months']
    row.applies_to_electric = data.get('applies_to_electric') in ('on', '1', True)
    row.is_active = data.get('is_active') in ('on', '1', True)
    row.sort_order = vehicles._integer(data.get('sort_order', 0), 'Порядок', 100000)
    db.session.add(row)
    return _audit(user, 'consumable_template_save', row)


def templates_for(vehicle):
    query = ConsumableTemplate.query.filter_by(is_active=True, template_set='truck' if vehicle.kind in ('truck', 'tractor_unit') else 'passenger')
    if vehicle.fuel_type == 'electric':
        query = query.filter_by(applies_to_electric=True)
    if vehicle.kind == 'trailer':
        query = query.filter(ConsumableTemplate.interval_months.isnot(None), ConsumableTemplate.interval_km.is_(None))
    return query.order_by(ConsumableTemplate.sort_order, ConsumableTemplate.id).all()


def save_item(user, vehicle_id, data, item_id=None):
    vehicle = vehicles.get_vehicle_for_user(user, vehicle_id, for_update=True)
    vehicles._writable(vehicle)
    row = VehicleMaintenanceItem.query.filter_by(id=item_id, vehicle_id=vehicle.id).first_or_404() if item_id else VehicleMaintenanceItem(vehicle_id=vehicle.id)
    if data.get('template_id') and not item_id:
        template_id = vehicles._integer(data['template_id'], 'Шаблон')
        template = next((t for t in templates_for(vehicle) if t.id == template_id), None)
        if template is None:
            abort(404)
        row.template_id, row.name = template.id, template.name
        row.interval_km = None if vehicle.kind == 'trailer' else template.interval_km
        row.interval_months = template.interval_months
    else:
        values = _intervals(data, vehicle.kind == 'trailer')
        row.name = _text(data, 'name', 150, True)
        row.interval_km, row.interval_months = values['interval_km'], values['interval_months']
    row.last_done_date = _day(data['last_done_date'], 'Последняя замена') if data.get('last_done_date') else None
    row.last_done_km = vehicles._integer(data['last_done_km'], 'Пробег') if data.get('last_done_km') else None
    if vehicle.kind == 'trailer' and row.last_done_km is not None:
        raise ValueError('У прицепа нет собственного пробега.')
    row.is_active = data.get('is_active', 'on') in ('on', '1', True)
    db.session.add(row)
    return _audit(user, 'vehicle_maintenance_save', row)


def add_months(day, months):
    number = day.year * 12 + day.month - 1 + months
    year, month = divmod(number, 12)
    month += 1
    if year > 9999:
        raise ValueError('Срок выходит за допустимый диапазон дат.')
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def item_state(item, mileage, day):
    due_km = item.last_done_km + item.interval_km if item.interval_km and item.last_done_km is not None else None
    due_date = add_months(item.last_done_date, item.interval_months) if item.interval_months and item.last_done_date else None
    states = []
    if due_km is not None and mileage is not None:
        remaining = due_km - mileage
        states.append('overdue' if remaining <= 0 else 'soon' if remaining * 10 <= item.interval_km else 'ok')
    if due_date:
        remaining = (due_date - day).days
        states.append('overdue' if remaining <= 0 else 'soon' if remaining <= 30 else 'ok')
    state = max(states, key={'ok': 0, 'soon': 1, 'overdue': 2}.get) if states else 'unknown'
    return dict(status=state, due_km=due_km, due_date=due_date, label=LABELS[state], color=COLORS[state])


def mileage_forecast(vehicle, due_km):
    readings = sorted(vehicle.readings, key=lambda r: r.reading_date)
    if due_km is None or len(readings) < 2:
        return None
    first, last = readings[0], readings[-1]
    days = (last.reading_date - first.reading_date).days
    distance = last.odometer_km - first.odometer_km
    if days < 30 or distance <= 0 or due_km <= last.odometer_km:
        return None
    forecast_days = ((due_km - last.odometer_km) * days + distance - 1) // distance
    if forecast_days > (last.reading_date.max - last.reading_date).days:
        return None
    return last.reading_date + timedelta(days=forecast_days)


def document_condition(vehicle):
    """Extension point for document deadlines in stage 5."""
    return 'ok'


def technical_condition(vehicle):
    states = [document_condition(vehicle)]
    states.extend(item_state(item, vehicles.current_mileage(vehicle), vehicles.today())['status'] for item in vehicle.maintenance_items if item.is_active)
    states.extend({'critical': 'overdue', 'attention': 'soon', 'minor': 'ok'}[d.severity] for d in vehicle.defects if d.status == 'open')
    return max(states, key=lambda state: {'unknown': 0, 'ok': 0, 'soon': 1, 'overdue': 2}[state])


def save_defect(user, vehicle_id, data, defect_id=None):
    vehicle = vehicles.get_vehicle_for_user(user, vehicle_id, for_update=True)
    vehicles._writable(vehicle)
    row = VehicleDefect.query.filter_by(id=defect_id, vehicle_id=vehicle.id).first_or_404() if defect_id else VehicleDefect(vehicle_id=vehicle.id, status='open')
    if data.get('severity') not in SEVERITIES:
        raise ValueError('Выберите серьёзность неисправности.')
    row.title, row.comment = _text(data, 'title', 150, True), _text(data, 'comment', 2000)
    row.severity, row.found_date = data['severity'], _day(data.get('found_date'), 'Дата обнаружения')
    if row.fixed_date and row.found_date > row.fixed_date:
        raise ValueError('Дата обнаружения позже устранения.')
    if row.severity == 'critical' and data.get('set_in_repair') == 'on':
        vehicle.status = 'in_repair'
    db.session.add(row)
    return _audit(user, 'vehicle_defect_save', row)


def record_service(user, vehicle_id, data):
    """Own the transaction: mileage, spending, replacements and fixes succeed together."""
    try:
        vehicle = vehicles.get_vehicle_for_user(user, vehicle_id, for_update=True)
        vehicles._writable(vehicle)
        day = _day(data.get('service_date'), 'Дата обслуживания')
        km = None if vehicle.kind == 'trailer' else vehicles._integer(data.get('odometer_km'), 'Пробег')
        item_ids = {vehicles._integer(key, 'Позиция') for key in data.getlist('item_ids')}
        defect_ids = {vehicles._integer(key, 'Неисправность') for key in data.getlist('defect_ids')}
        items = VehicleMaintenanceItem.query.filter_by(vehicle_id=vehicle.id, is_active=True).filter(VehicleMaintenanceItem.id.in_(item_ids)).all()
        defects = VehicleDefect.query.filter_by(vehicle_id=vehicle.id, status='open').filter(VehicleDefect.id.in_(defect_ids)).all()
        if {i.id for i in items} != item_ids or {d.id for d in defects} != defect_ids:
            abort(404)
        if any((i.last_done_date and i.last_done_date > day) or (km is not None and i.last_done_km is not None and i.last_done_km > km) for i in items):
            raise ValueError('Обслуживание не может предшествовать последней замене.')
        if any(d.found_date > day for d in defects):
            raise ValueError('Обслуживание не может быть раньше обнаружения неисправности.')
        row = VehicleServiceRecord(vehicle_id=vehicle.id, service_date=day, odometer_km=km, comment=_text(data, 'comment', 2000), items=items)
        db.session.add(row)
        if km is not None:
            existing = VehicleOdometerReading.query.filter_by(vehicle_id=vehicle.id, reading_date=day).first()
            if existing:
                if existing.odometer_km != km:
                    raise ValueError('На эту дату уже указан другой пробег.')
                existing.source = 'service'
            else:
                vehicles.add_odometer_reading(user, vehicle.id, day.isoformat(), km, source='service', commit=False)
        for item in items:
            item.last_done_date, item.last_done_km = day, km
        db.session.flush()
        for defect in defects:
            defect.status, defect.fixed_date, defect.service_record_id = 'fixed', day, row.id
        if data.get('amount', '').strip():
            spend = costs.create_vehicle_spend(user, vehicle, data.get('payer'), amount=data['amount'], cost_date=day.isoformat(), kind='service', comment=row.comment, commit=False)
            if isinstance(spend, Expense):
                row.expense_id = spend.id
            else:
                row.vehicle_cost_id = spend.id
        # An old service entry must also clear the mileage reminder until tomorrow.
        vehicle.mileage_snooze_until = vehicles.today() + timedelta(days=1)
        return _audit(user, 'vehicle_service_create', row)
    except Exception:
        db.session.rollback()
        raise


def detail(vehicle):
    mileage, day = vehicles.current_mileage(vehicle), vehicles.today()
    items = [(item, item_state(item, mileage, day)) for item in vehicle.maintenance_items]
    return dict(maintenance_items=items, maintenance_templates=templates_for(vehicle),
                condition=technical_condition(vehicle), maintenance_labels=LABELS,
                maintenance_colors=COLORS, defect_severities=SEVERITIES,
                forecasts={item.id: mileage_forecast(vehicle, state['due_km']) for item, state in items})
