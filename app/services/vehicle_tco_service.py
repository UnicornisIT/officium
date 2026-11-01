"""Owner-scoped, batched TCO calculations; monetary values stay Decimal."""
import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.models import VehicleDocument, VehicleOdometerReading, VehicleRefueling
from app.services import vehicle_cost_service as costs, vehicle_service as vehicles
from app.services.vehicle_fuel_service import _stats
from app.utils import parse_date, record_activity

ZERO = Decimal(0)


def _money(value):
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def _month(day, offset=0):
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def tco_report(user, vehicle_ids=None, date_from=None, date_to=None):
    today = vehicles.today()
    start = parse_date(date_from, 'Начало') if date_from else date(today.year, 1, 1)
    end = parse_date(date_to, 'Конец') if date_to else date(today.year, 12, 31)
    if end < start:
        raise ValueError('Конец периода раньше начала.')
    if (end.year - start.year) * 12 + end.month - start.month >= 120:
        raise ValueError('Выберите период не более 10 лет.')
    # This validates every requested ID before reading any financial rows.
    source = costs.vehicle_report(user, start, end, vehicle_ids)
    ids = source['selected_ids']
    months = {}
    day = _month(start)
    while day <= end:
        months[day] = dict(month=day, total_self=ZERO, total_organization=ZERO, total=ZERO, kilometers=0)
        day = _month(day, 1)
    readings = defaultdict(list)
    for row in VehicleOdometerReading.query.filter(VehicleOdometerReading.vehicle_id.in_(ids), VehicleOdometerReading.reading_date.between(start, end)).order_by(VehicleOdometerReading.reading_date).all():
        readings[row.vehicle_id].append(row)
    fuel = defaultdict(list)
    for row in VehicleRefueling.query.filter(VehicleRefueling.vehicle_id.in_(ids), VehicleRefueling.refuel_date.between(start, end)).order_by(VehicleRefueling.refuel_date).all():
        fuel[row.vehicle_id].append(row)
    # Linked documents/refuelings already have an Expense or VehicleCost.
    # Unlinked document amounts are attributed to self on the issue date only.
    for doc in VehicleDocument.query.filter(VehicleDocument.vehicle_id.in_(ids), VehicleDocument.issued_date.between(start, end), VehicleDocument.expense_id.is_(None), VehicleDocument.vehicle_cost_id.is_(None), VehicleDocument.amount.is_not(None)).all():
        source['rows'].append(dict(vehicle_id=doc.vehicle_id, date=doc.issued_date, amount=doc.amount, payer='self', kind='insurance'))
    per_vehicle = {}
    for vehicle in source['vehicles']:
        if vehicle.id not in ids:
            continue
        samples = readings[vehicle.id] if vehicle.kind != 'trailer' else []
        km = max(0, samples[-1].odometer_km - samples[0].odometer_km) if len(samples) >= 2 else 0
        per_vehicle[vehicle.id] = dict(id=vehicle.id, name=vehicle.name, kind=vehicle.kind, total_self=ZERO, total_organization=ZERO, total=ZERO, kilometers=km, by_kind={key: ZERO for key in costs.KINDS}, **_stats(fuel[vehicle.id]))
        grouped = defaultdict(list)
        for sample in samples:
            grouped[_month(sample.reading_date)].append(sample.odometer_km)
        for month, values in grouped.items():
            months[month]['kilometers'] += max(0, values[-1] - values[0]) if len(values) >= 2 else 0
    for row in source['rows']:
        bucket = per_vehicle[row['vehicle_id']]
        key = 'total_' + row['payer']
        bucket[key] += row['amount']
        bucket['total'] += row['amount']
        bucket['by_kind'][row['kind'] if row['kind'] in costs.KINDS else 'other'] += row['amount']
        months[_month(row['date'])][key] += row['amount']
        months[_month(row['date'])]['total'] += row['amount']
    totals = {key: sum((v[key] for v in per_vehicle.values()), ZERO if key != 'kilometers' else 0) for key in ('total_self', 'total_organization', 'total', 'kilometers')}
    for bucket in [*per_vehicle.values(), totals, *months.values()]:
        bucket['cost_per_km'] = _money(bucket['total'] / bucket['kilometers']) if bucket['kilometers'] else None
    return dict(period=dict(start=start, end=end), vehicles=list(per_vehicle.values()), totals=totals, monthly=list(months.values()), selected_ids=ids, available_vehicles=source['vehicles'])


def compare_vehicles(user, vehicle_ids=None, date_from=None, date_to=None):
    report = tco_report(user, vehicle_ids, date_from, date_to)
    report['vehicles'].sort(key=lambda row: (-row['total'], row['id']))
    return report


def forecast_next_month(user, vehicle_id):
    """Use up to six completed months, including zero months after first spend.

    For 3–5 months split history into an early and late half; six uses 3+3.
    A zero early baseline or trend outside [-20%, +50%] suppresses a forecast.
    """
    end = _month(vehicles.today()) - timedelta(days=1)
    report = tco_report(user, [vehicle_id], _month(end, -5), end)
    values = [row['total'] for row in report['monthly']]
    while values and not values[0]:
        values.pop(0)
    if len(values) < 3:
        return None
    split = len(values) // 2
    early = sum(values[:split], ZERO) / split
    late = sum(values[split:], ZERO) / (len(values) - split)
    if not early:
        return None
    trend = (late - early) / early
    if not Decimal('-.2') <= trend <= Decimal('.5'):
        return None
    return _money(sum(values, ZERO) / len(values) * (1 + trend))


def cost_per_km_history(user, vehicle_id, months=12):
    if not isinstance(months, int) or not 1 <= months <= 120:
        raise ValueError('Укажите от 1 до 120 месяцев.')
    end = _month(vehicles.today(), 1) - timedelta(days=1)
    return [(r['month'], r['cost_per_km']) for r in tco_report(user, [vehicle_id], _month(end, 1 - months), end)['monthly']]


def report_csv(user, report):
    vehicles._user(user)
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Автомобиль', 'Личные, ₽', 'Организация, ₽', 'Всего, ₽', 'Пробег, км', 'Стоимость км, ₽', 'Литры', 'Расход, л/100 км', 'Средняя цена литра, ₽', 'Заправки'])
    def number(value, precision=2):
        return '' if value is None else format(value, f'.{precision}f').replace('.', ',')
    for row in report['vehicles']:
        name = row['name']
        if name.lstrip().startswith(('=', '+', '-', '@')):
            name = "'" + name
        writer.writerow([name, *[number(row[key]) for key in ('total_self', 'total_organization', 'total')], row['kilometers'], number(row['cost_per_km']), number(row['total_liters'], 3), number(row['avg_consumption']), number(row['avg_price_per_liter']), row['refuelings_count']])
    total = report['totals']
    writer.writerow(['Итого', *[number(total[k]) for k in ('total_self', 'total_organization', 'total')], total['kilometers'], number(total['cost_per_km']), '', '', '', ''])
    record_activity('vehicle_tco_export', user, entity_type='vehicle_tco_export')
    return ('\ufeff' + output.getvalue()).encode('utf-8')
