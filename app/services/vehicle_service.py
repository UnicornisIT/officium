"""Owner-scoped vehicle operations. Audit records contain identifiers only."""
import re
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from flask import abort, current_app
from sqlalchemy import or_

from app.models import Expense, RegistrationCountry, Vehicle, VehicleOdometerReading
from app.services.personal_access_service import can_use_personal_cabinet
from app.time_utils import utc_now
from app.utils import parse_date, record_activity
from extensions import db

KINDS = {'passenger': 'Легковая', 'truck': 'Грузовик', 'tractor_unit': 'Тягач', 'trailer': 'Прицеп'}
BODIES = dict(sedan='Седан', hatchback='Хэтчбек', wagon='Универсал', suv='Внедорожник', minivan='Минивэн', pickup='Пикап', van='Фургон', tractor_unit='Тягач', flatbed='Бортовой', trailer='Прицеп')
FUELS = dict(petrol='Бензин', diesel='Дизель', gas='Газ', hybrid='Гибрид', electric='Электричество', none='Без топлива')
STATUSES = dict(active='Активно', in_repair='В ремонте')
ARCHIVE_REASONS = dict(sold='Продано', written_off='Списано', other='Другое')


def today():
    name = current_app.config['APP_TIMEZONE']
    zone = timezone.utc if name.upper() == 'UTC' else timezone(timedelta(hours=3)) if name == 'Europe/Moscow' else ZoneInfo(name)
    return utc_now().astimezone(zone).date()


def require_enabled():
    if not current_app.config.get('VEHICLES_ENABLED'):
        abort(404)


def _user(user, *, write=False):
    require_enabled()
    if not can_use_personal_cabinet(user) or getattr(user, 'is_local_test_user', False):
        abort(404)


def get_vehicle_for_user(user, vehicle_id, *, for_update=False):
    _user(user)
    query = Vehicle.query.filter_by(user_id=user.id, id=vehicle_id)
    if for_update:
        query = query.with_for_update()
    return query.first_or_404()


def _writable(vehicle):
    if vehicle.archived_at:
        raise ValueError('Автомобиль в архиве: сначала восстановите его.')


def _audit(user, action, vehicle, commit):
    db.session.flush()
    record_activity(action, user, entity_type='vehicle', entity_id=vehicle.id, commit=False)
    if commit:
        db.session.commit()
    return vehicle


def normalize_plate(plate, country):
    plate = str(plate or '').strip().upper()
    if not plate:
        return None
    if country and country.code == 'RU':
        plate = ''.join(plate.split()).translate(str.maketrans('ABEKMHOPCTYX', 'АВЕКМНОРСТУХ'))
        if not re.fullmatch(r'[АВЕКМНОРСТУХ][0-9]{3}[АВЕКМНОРСТУХ]{2}[0-9]{2,3}', plate):
            raise ValueError('Номер РФ: используйте формат А123ВС77 или А123ВС777.')
    elif len(plate) > 12:
        raise ValueError('Госномер должен содержать не более 12 символов.')
    return plate


def _integer(value, label, maximum=2147483647):
    if not re.fullmatch(r'[0-9]+', str(value)) or not 0 <= int(value) <= maximum:
        raise ValueError(f'{label}: введите целое неотрицательное число.')
    return int(value)


def _values(user, data, existing=None):
    result = {}
    for key, length in [('name', 150), ('make', 80), ('model', 80), ('year', 20)]:
        value = str(data.get(key, getattr(existing, key, '') or '')).strip()
        if len(value) > length or (key == 'name' and not value):
            raise ValueError('Проверьте название, марку, модель и год автомобиля.')
        result[key] = value or None
    duplicate = Vehicle.query.filter_by(user_id=user.id, name=result['name'])
    if existing:
        duplicate = duplicate.filter(Vehicle.id != existing.id)
    if duplicate.first():
        raise ValueError('Автомобиль с таким названием уже существует.')
    for key, choices, default in [('kind', KINDS, 'passenger'), ('body_type', BODIES, 'sedan'), ('fuel_type', FUELS, 'petrol'), ('status', STATUSES, 'active')]:
        value = data.get(key, getattr(existing, key, default))
        if value not in choices:
            raise ValueError('Выберите корректный тип, кузов, топливо и статус.')
        result[key] = value
    if existing and existing.kind != result['kind'] and (existing.readings or existing.trailers or existing.maintenance_items or existing.service_records or existing.defects):
        raise ValueError('Нельзя менять тип автомобиля с историей пробега, обслуживания, неисправностей или прицепами.')
    if result['kind'] == 'trailer':
        result.update(body_type='trailer', fuel_type='none')
    if result['kind'] == 'tractor_unit':
        result['body_type'] = 'tractor_unit'
    color = data.get('color', getattr(existing, 'color', '#5276ba'))
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('Цвет должен быть в формате #RRGGBB.')
    result['color'] = color
    vin = str(data.get('vin', getattr(existing, 'vin', '') or '')).strip().upper()
    if vin and not re.fullmatch('[A-HJ-NPR-Z0-9]{17}', vin):
        raise ValueError('VIN: 17 латинских букв и цифр, без I, O, Q.')
    result['vin'] = vin or None
    country_id = data.get('registration_country_id', getattr(existing, 'registration_country_id', None))
    country = None
    if country_id:
        country = db.session.get(RegistrationCountry, _integer(country_id, 'Страна'))
        if not country or (not country.is_active and (not existing or country.id != existing.registration_country_id)):
            raise ValueError('Выберите действующую страну учёта.')
    result['registration_country_id'] = country.id if country else None
    result['plate'] = normalize_plate(data.get('plate', getattr(existing, 'plate', None)), country)
    if result['plate'] and not country:
        raise ValueError('Укажите страну учёта для госномера.')
    company = data.get('is_company', getattr(existing, 'is_company', False))
    result['is_company'] = company is True or company in ('on', 'true', '1')
    payer = data.get('default_payer', getattr(existing, 'default_payer', 'self'))
    if payer not in ('self', 'organization') or (payer == 'organization' and not result['is_company']):
        raise ValueError('Плательщик «организация» доступен только для служебного автомобиля.')
    result['default_payer'] = payer
    result['mileage_reminder_days'] = _integer(data.get('mileage_reminder_days', getattr(existing, 'mileage_reminder_days', 30)), 'Интервал', 36500)
    tractor_id = data.get('tractor_id', getattr(existing, 'tractor_id', None))
    result['tractor_id'] = None
    if tractor_id:
        tractor = get_vehicle_for_user(user, _integer(tractor_id, 'Тягач'))
        _writable(tractor)
        if result['kind'] != 'trailer' or tractor.kind != 'tractor_unit' or (existing and tractor.id == existing.id):
            raise ValueError('Прицеп можно привязать только к своему тягачу.')
        result['tractor_id'] = tractor.id
    return result


def create_vehicle(user, data, *, commit=True):
    _user(user, write=True)
    vehicle = Vehicle(user_id=user.id, **_values(user, data))
    db.session.add(vehicle)
    return _audit(user, 'vehicle_create', vehicle, commit)


def create_with_trailer(user, data):
    vehicle = create_vehicle(user, data, commit=False)
    if data.get('trailer_name', '').strip():
        if vehicle.kind != 'tractor_unit':
            raise ValueError('Добавить прицеп вместе с автомобилем можно только для тягача.')
        create_vehicle(user, dict(name=data['trailer_name'], kind='trailer', tractor_id=vehicle.id), commit=False)
    db.session.commit()
    return vehicle


def update_mileage_batch(user, data):
    ids = data.getlist('vehicle_ids')
    vehicles = [get_vehicle_for_user(user, _integer(key, 'Автомобиль')) for key in ids]
    for vehicle in vehicles:
        _writable(vehicle)
    if data.get('action') == 'snooze':
        snooze(user, ids)
    else:
        for vehicle in vehicles:
            value = data.get(f'km_{vehicle.id}', '').strip()
            if value:
                add_odometer_reading(user, vehicle.id, today().isoformat(), value, commit=False)
        db.session.commit()


def update_vehicle(user, vehicle_id, data, *, commit=True):
    vehicle = get_vehicle_for_user(user, vehicle_id, for_update=True)
    _writable(vehicle)
    values = _values(user, data, vehicle)
    for key, value in values.items():
        setattr(vehicle, key, value)
    return _audit(user, 'vehicle_update', vehicle, commit)


def archive_vehicle(user, vehicle_id, reason, note='', *, commit=True):
    vehicle = get_vehicle_for_user(user, vehicle_id, for_update=True)
    _writable(vehicle)
    if reason not in ARCHIVE_REASONS or len(note) > 2000:
        raise ValueError('Выберите причину архивации; примечание — до 2000 символов.')
    if any(not item.archived_at for item in vehicle.trailers):
        raise ValueError('Сначала отвяжите или архивируйте прицепы тягача.')
    vehicle.archived_at, vehicle.archive_reason, vehicle.archive_note = utc_now(), reason, note.strip() or None
    return _audit(user, 'vehicle_archive', vehicle, commit)


def restore_vehicle(user, vehicle_id, *, commit=True):
    vehicle = get_vehicle_for_user(user, vehicle_id, for_update=True)
    if not vehicle.archived_at:
        raise ValueError('Автомобиль уже активен.')
    if vehicle.tractor_id:
        tractor = get_vehicle_for_user(user, vehicle.tractor_id, for_update=True)
        if tractor.archived_at:
            raise ValueError('Сначала восстановите тягач прицепа.')
    vehicle.archived_at = vehicle.archive_reason = vehicle.archive_note = None
    return _audit(user, 'vehicle_restore', vehicle, commit)


def add_odometer_reading(user, vehicle_id, reading_date, odometer_km, source='manual', *, commit=True):
    vehicle = get_vehicle_for_user(user, vehicle_id, for_update=True)
    _writable(vehicle)
    if vehicle.kind == 'trailer':
        raise ValueError('У прицепа нет собственного пробега.')
    day = parse_date(reading_date, 'Дата замера', required=True)
    if day > today():
        raise ValueError('Дата замера не может быть в будущем.')
    km = _integer(odometer_km, 'Пробег')
    if source not in ('manual', 'service', 'fuel', 'bot'):
        raise ValueError('Некорректный источник замера.')
    readings = VehicleOdometerReading.query.filter_by(vehicle_id=vehicle.id)
    if readings.filter_by(reading_date=day).first():
        raise ValueError('Замер на эту дату уже существует.')
    if readings.filter(VehicleOdometerReading.reading_date < day, VehicleOdometerReading.odometer_km > km).first():
        raise ValueError('Пробег не может быть меньше замера на более раннюю дату.')
    if readings.filter(VehicleOdometerReading.reading_date > day, VehicleOdometerReading.odometer_km < km).first():
        raise ValueError('Пробег не может превышать замер на более позднюю дату.')
    row = VehicleOdometerReading(vehicle_id=vehicle.id, reading_date=day, odometer_km=km, source=source)
    db.session.add(row)
    _audit(user, 'vehicle_mileage', vehicle, commit)
    return row


def current_mileage(vehicle):
    row = VehicleOdometerReading.query.filter_by(vehicle_id=vehicle.id).order_by(VehicleOdometerReading.reading_date.desc()).first()
    return row.odometer_km if row else None


def needs_mileage_update(vehicle, today):
    if vehicle.archived_at or vehicle.status != 'active' or vehicle.kind == 'trailer' or vehicle.mileage_reminder_days <= 0:
        return False
    if vehicle.mileage_snooze_until and vehicle.mileage_snooze_until > today:
        return False
    row = VehicleOdometerReading.query.filter_by(vehicle_id=vehicle.id).order_by(VehicleOdometerReading.reading_date.desc()).first()
    return row is None or (today - row.reading_date).days > vehicle.mileage_reminder_days


def due_vehicles(user):
    _user(user)
    return [v for v in Vehicle.query.filter_by(user_id=user.id, archived_at=None).all() if needs_mileage_update(v, today())]


def snooze(user, vehicle_ids, *, commit=True):
    vehicles = [get_vehicle_for_user(user, key) for key in vehicle_ids]
    for vehicle in vehicles:
        _writable(vehicle)
    for vehicle in vehicles:
        vehicle.mileage_snooze_until = today() + timedelta(days=3)
        _audit(user, 'vehicle_snooze', vehicle, False)
    if commit:
        db.session.commit()


def list_vehicles(user, args):
    _user(user)
    query = Vehicle.query.filter_by(user_id=user.id)
    archive = args.get('archive', 'active')
    if archive != 'all':
        query = query.filter(Vehicle.archived_at.isnot(None) if archive == 'archived' else Vehicle.archived_at.is_(None))
    for key in ('status', 'kind'):
        if args.get(key):
            query = query.filter(getattr(Vehicle, key) == args[key])
    if args.get('q'):
        search = '%' + args['q'].strip()[:150] + '%'
        query = query.filter(or_(Vehicle.name.ilike(search), Vehicle.make.ilike(search), Vehicle.plate.ilike(search)))
    if args.get('attention') == '1':
        from app.services.vehicle_maintenance_service import technical_condition
        ids = [v.id for v in query.all() if technical_condition(v) in ('soon', 'overdue')]
        query = query.filter(Vehicle.id.in_(ids))
    return query.order_by(Vehicle.created_at.desc(), Vehicle.id.desc()).paginate(page=max(args.get('page', 1, type=int) or 1, 1), per_page=12, error_out=False)


def selectable_vehicles(user):
    _user(user)
    return Vehicle.query.filter_by(user_id=user.id, archived_at=None, status='active').order_by(Vehicle.name).all()


def form_options(user):
    _user(user)
    return dict(countries=RegistrationCountry.query.order_by(RegistrationCountry.sort_order, RegistrationCountry.name).all(),
        tractors=Vehicle.query.filter_by(user_id=user.id, kind='tractor_unit', archived_at=None).order_by(Vehicle.name).all())


def vehicle_expenses(user, vehicle_id):
    vehicle = get_vehicle_for_user(user, vehicle_id)
    return Expense.query.filter_by(user_id=user.id, vehicle_id=vehicle.id).order_by(Expense.expense_date.desc(), Expense.id.desc()).all()


def save_country(user, data, country_id=None):
    require_enabled()
    if not getattr(user, 'is_superadmin', False):
        abort(403)
    country = RegistrationCountry.query.filter_by(id=country_id).first_or_404() if country_id else RegistrationCountry()
    code, name = str(data.get('code', country.code or '')).strip().upper(), str(data.get('name', country.name or '')).strip()
    if not re.fullmatch('[A-Z]{2,8}', code) or not name or len(name) > 100:
        raise ValueError('Введите код (2–8 латинских букв) и название страны (до 100 символов).')
    if country_id and code != country.code:
        raise ValueError('Код существующей страны менять нельзя.')
    duplicate = RegistrationCountry.query.filter_by(code=code).first()
    if duplicate and duplicate.id != country.id:
        raise ValueError('Страна с таким кодом уже существует.')
    country.code, country.name = code, name
    country.is_active = data.get('is_active') in ('on', 'true', '1', True)
    country.strict_format = code == 'RU'
    country.sort_order = _integer(data.get('sort_order', country.sort_order or 0), 'Порядок', 100000)
    db.session.add(country)
    db.session.flush()
    record_activity('registration_country_save', user, entity_type='registration_country', entity_id=country.id, commit=False)
    db.session.commit()
    return country
