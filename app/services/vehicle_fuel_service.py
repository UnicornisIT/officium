"""Fuel history and atomic synchronization of mileage and spending."""
from decimal import Decimal, ROUND_HALF_UP

from flask import abort
from app.models import Expense, VehicleCost, VehicleRefueling, VehicleOdometerReading, VehicleServiceRecord
from app.services import vehicle_service as vehicles, vehicle_cost_service as costs, expense_service
from app.utils import parse_date, parse_decimal, record_activity
from extensions import db


def _number(data, key, precision, maximum):
    value = parse_decimal(data.get(key), key, precision=Decimal(precision))
    if value <= 0 or value > Decimal(maximum):
        raise ValueError('Укажите положительные объём, цену и сумму в допустимом диапазоне.')
    return value


def _text(data, key, limit):
    value = str(data.get(key) or '').strip()
    if len(value) > limit:
        raise ValueError(f'Поле {key}: не более {limit} символов.')
    return value or None


def get_refueling(vehicle, refueling_id):
    return VehicleRefueling.query.filter_by(id=refueling_id, vehicle_id=vehicle.id).first_or_404()


def record_refueling(user, vehicle_id, data, *, refueling_id=None):
    try:
        vehicle = vehicles.get_vehicle_for_user(user, vehicle_id, for_update=True)
        vehicles._writable(vehicle)
        if vehicle.kind == 'trailer':
            abort(404)
        if vehicle.status != 'active':
            raise ValueError('Заправки доступны только для активного автомобиля.')
        row = get_refueling(vehicle, refueling_id) if refueling_id else VehicleRefueling(vehicle_id=vehicle.id)
        day = parse_date(data.get('refuel_date'), 'Дата заправки', required=True)
        if day > vehicles.today():
            raise ValueError('Дата заправки не может быть в будущем.')
        liters = _number(data, 'liters', '.001', '9999999.999')
        price = _number(data, 'price_per_liter', '.01', '99999999.99')
        total = _number(data, 'total_amount', '.01', '9999999999.99')
        if abs(total - liters * price) > Decimal('.50'):
            raise ValueError('Итог отличается от объёма × цены более чем на 0,50 ₽.')
        payer = data.get('payer') or vehicle.default_payer
        if payer not in ('self', 'organization') or (payer == 'organization' and not vehicle.is_company):
            raise ValueError('Организация может оплачивать только служебное авто.')
        duplicate = VehicleRefueling.query.filter_by(vehicle_id=vehicle.id, refuel_date=day)
        if row.id:
            duplicate = duplicate.filter(VehicleRefueling.id != row.id)
        if duplicate.first():
            raise ValueError('Заправка на эту дату уже существует. Объедините заправки за день.')
        km = vehicles._integer(data['odometer_km'], 'Пробег') if data.get('odometer_km') not in (None, '') else None
        station, comment = _text(data, 'station_name', 150), _text(data, 'comment', 2000)
        # Only move fuel-owned readings; shared manual/service measurements remain intact.
        if row.id and (row.refuel_date != day or row.odometer_km != km):
            old = VehicleOdometerReading.query.filter_by(vehicle_id=vehicle.id, reading_date=row.refuel_date).first()
            shared = VehicleServiceRecord.query.filter_by(vehicle_id=vehicle.id, service_date=row.refuel_date).first()
            if old and old.source == 'fuel' and old.odometer_km == row.odometer_km and not shared:
                db.session.delete(old)
                db.session.flush()
        if km is not None:
            reading = VehicleOdometerReading.query.filter_by(vehicle_id=vehicle.id, reading_date=day).first()
            if reading and reading.odometer_km != km:
                raise ValueError('На эту дату уже указан другой пробег.')
            if not reading:
                vehicles.add_odometer_reading(user, vehicle.id, day.isoformat(), km, source='fuel', commit=False)
        old_spend = db.session.get(Expense, row.expense_id) if row.expense_id else db.session.get(VehicleCost, row.vehicle_cost_id) if row.vehicle_cost_id else None
        title = f'Заправка {station or "АЗС"}'[:150]
        if old_spend and row.payer == payer:
            if isinstance(old_spend, Expense):
                expense_service.update_expense(user_id=user.id, expense_id=old_spend.id, vehicle_id=vehicle.id, amount=total, category='transport', title=title, expense_date=day.isoformat(), comment=comment, payment_method=old_spend.payment_method, commit=False)
            else:
                if old_spend.reimbursed_at and day > old_spend.reimbursed_at:
                    raise ValueError('Заправка не может быть позже возмещения.')
                old_spend.amount, old_spend.cost_date = total, day
                old_spend.title, old_spend.comment = title, comment
        else:
            if old_spend:
                if isinstance(old_spend, VehicleCost) and (old_spend.paid_by_driver or old_spend.reimbursed_at):
                    raise ValueError('Перед сменой плательщика снимите отметки оплаты и возмещения.')
                row.expense_id = row.vehicle_cost_id = None
                db.session.flush()
                if isinstance(old_spend, Expense):
                    expense_service.delete_expense(user_id=user.id, expense_id=old_spend.id, commit=False)
                else:
                    db.session.delete(old_spend)
            spend = costs.create_vehicle_spend(user, vehicle, payer, amount=total, cost_date=day.isoformat(), kind='fuel', title=title, comment=comment, commit=False)
            row.expense_id = spend.id if isinstance(spend, Expense) else None
            row.vehicle_cost_id = spend.id if isinstance(spend, VehicleCost) else None
        row.refuel_date, row.odometer_km = day, km
        row.liters, row.price_per_liter, row.total_amount = liters, price, total
        row.payer, row.station_name, row.comment = payer, station, comment
        row.is_full_tank = data.get('is_full_tank', True) in (True, 'on', '1', 'true')
        db.session.add(row)
        db.session.flush()
        record_activity('vehicle_refueling_update' if refueling_id else 'vehicle_refueling_create', user, entity_type='vehicle_refueling', entity_id=row.id, commit=False)
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise


def _consumption(previous, current):
    if not previous or not previous.is_full_tank or not current.is_full_tank or previous.odometer_km is None or current.odometer_km is None or current.odometer_km <= previous.odometer_km:
        return None
    return (current.liters * 100 / (current.odometer_km - previous.odometer_km)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def consumption(vehicle, refueling):
    previous = VehicleRefueling.query.filter(VehicleRefueling.vehicle_id == vehicle.id, VehicleRefueling.refuel_date < refueling.refuel_date).order_by(VehicleRefueling.refuel_date.desc()).first()
    return _consumption(previous, refueling)


def fuel_history(vehicle):
    rows = VehicleRefueling.query.filter_by(vehicle_id=vehicle.id).order_by(VehicleRefueling.refuel_date).all()
    return [dict(refueling=row, consumption=_consumption(rows[i-1] if i else None, row)) for i, row in enumerate(rows)]


def _stats(rows):
    liters = sum((r.liters for r in rows), Decimal(0))
    full = [r for r in rows if r.is_full_tank and r.odometer_km is not None]
    average = None
    if len(full) >= 2 and full[-1].odometer_km > full[0].odometer_km:
        used = sum((r.liters for r in rows if full[0].refuel_date < r.refuel_date <= full[-1].refuel_date), Decimal(0))
        average = (used * 100 / (full[-1].odometer_km - full[0].odometer_km)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return dict(total_liters=liters, total_amount=sum((r.total_amount for r in rows), Decimal(0)), avg_price_per_liter=(sum((r.liters*r.price_per_liter for r in rows), Decimal(0))/liters).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) if liters else None, avg_consumption=average, refuelings_count=len(rows))


def fuel_stats(vehicle, date_from, date_to):
    start = parse_date(date_from, 'Начало периода', required=True)
    end = parse_date(date_to, 'Конец периода', required=True)
    if end < start:
        raise ValueError('Конец периода раньше начала.')
    return _stats([item['refueling'] for item in fuel_history(vehicle) if start <= item['refueling'].refuel_date <= end])


def detail(vehicle, date_from=None, date_to=None):
    history = fuel_history(vehicle)
    today = vehicles.today()
    start = parse_date(date_from, 'Начало периода') if date_from else None
    end = parse_date(date_to, 'Конец периода') if date_to else None
    if start and end and end < start:
        raise ValueError('Конец периода раньше начала.')
    selected = [r for r in history if (not start or r['refueling'].refuel_date >= start) and (not end or r['refueling'].refuel_date <= end)]
    return dict(fuel_history=selected, fuel_summary=_stats([r['refueling'] for r in selected]), fuel_recent=_stats([r['refueling'] for r in history[-5:]]), fuel_month=_stats([r['refueling'] for r in history if today.replace(day=1) <= r['refueling'].refuel_date <= today]))
