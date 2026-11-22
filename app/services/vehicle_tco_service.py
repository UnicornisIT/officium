"""Owner-scoped, batched TCO calculations; monetary values stay Decimal."""

import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import and_, func

from app.models import VehicleDocument, VehicleOdometerReading, VehicleRefueling
from app.services import vehicle_cost_service as costs, vehicle_service as vehicles
from app.services.vehicle_fuel_service import _stats
from app.utils import parse_date, record_activity

ZERO = Decimal(0)
LONG_GAP_DAYS = 90
MILEAGE_FLAGS = (
    'has_mileage',
    'estimated_mileage',
    'odometer_decrease',
    'long_mileage_gap',
)


def _money(value):
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def _month(day, offset=0):
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def _mileage_bucket():
    return dict(kilometers=0, **dict.fromkeys(MILEAGE_FLAGS, False))


def _odometer_samples(ids, start, end):
    samples = defaultdict(list)
    if not ids:
        return samples
    reading = VehicleOdometerReading
    selected = reading.query.filter(reading.vehicle_id.in_(ids))
    within = selected.filter(reading.reading_date.between(start, end))

    def nearest(condition, aggregate):
        dates = (
            selected.with_entities(
                reading.vehicle_id, aggregate(reading.reading_date).label('day')
            )
            .filter(condition)
            .group_by(reading.vehicle_id)
            .subquery()
        )
        return reading.query.join(
            dates,
            and_(
                reading.vehicle_id == dates.c.vehicle_id,
                reading.reading_date == dates.c.day,
            ),
        )

    rows = within.union_all(
        nearest(reading.reading_date < start, func.max),
        nearest(reading.reading_date > end, func.min),
    ).order_by(reading.vehicle_id, reading.reading_date)
    for row in rows:
        samples[row.vehicle_id].append(row)
    return samples


def _add_mileage(bucket, first_day, last_day, days, kilometers):
    if kilometers < 0:
        bucket['odometer_decrease'] = True
        return
    bucket['has_mileage'] = True
    bucket['long_mileage_gap'] |= days > LONG_GAP_DAYS
    bucket['estimated_mileage'] |= kilometers > 0 and last_day - first_day < days

    # Round cumulative distance at each boundary so adjacent shares add up exactly.
    first_km = (2 * kilometers * first_day + days) // (2 * days)
    last_km = (2 * kilometers * last_day + days) // (2 * days)
    bucket['kilometers'] += last_km - first_km


def _allocate_mileage(samples, start, end_exclusive, months):
    period = _mileage_bucket()
    for previous, current in zip(samples, samples[1:]):
        first, last = previous.reading_date, current.reading_date
        # Readings bound whole days starting on the first date and ending before the second.
        left, right = max(first, start), min(last, end_exclusive)
        if left >= right:
            continue
        days = (last - first).days
        kilometers = current.odometer_km - previous.odometer_km
        _add_mileage(
            period, (left - first).days, (right - first).days, days, kilometers
        )
        last_month = _month(right - timedelta(days=1))
        while left < right:
            month = _month(left)
            boundary = right if month == last_month else _month(month, 1)
            _add_mileage(
                months[month],
                (left - first).days,
                (boundary - first).days,
                days,
                kilometers,
            )
            left = boundary
    return period


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
    selected = [vehicle for vehicle in source['vehicles'] if vehicle.id in ids]
    readings = _odometer_samples(
        [vehicle.id for vehicle in selected if vehicle.kind != 'trailer'], start, end
    )
    end_exclusive = end + timedelta(days=1) if end < date.max else end
    months = {}
    day = _month(start)
    last_month = _month(end)
    while True:
        months[day] = dict(
            month=day,
            total_self=ZERO,
            total_organization=ZERO,
            total=ZERO,
            **_mileage_bucket(),
        )
        if day == last_month:
            break
        day = _month(day, 1)
    fuel = defaultdict(list)
    for row in (
        VehicleRefueling.query.filter(
            VehicleRefueling.vehicle_id.in_(ids),
            VehicleRefueling.refuel_date.between(start, end),
        )
        .order_by(VehicleRefueling.refuel_date)
        .all()
    ):
        fuel[row.vehicle_id].append(row)
    # Linked documents/refuelings already have an Expense or VehicleCost.
    # Unlinked document amounts are attributed to self on the issue date only.
    for doc in VehicleDocument.query.filter(
        VehicleDocument.vehicle_id.in_(ids),
        VehicleDocument.issued_date.between(start, end),
        VehicleDocument.expense_id.is_(None),
        VehicleDocument.vehicle_cost_id.is_(None),
        VehicleDocument.amount.is_not(None),
    ).all():
        source['rows'].append(
            dict(
                vehicle_id=doc.vehicle_id,
                date=doc.issued_date,
                amount=doc.amount,
                payer='self',
                kind='insurance',
            )
        )
    per_vehicle = {}
    for vehicle in selected:
        mileage = _allocate_mileage(readings[vehicle.id], start, end_exclusive, months)
        per_vehicle[vehicle.id] = dict(
            id=vehicle.id,
            name=vehicle.name,
            kind=vehicle.kind,
            total_self=ZERO,
            total_organization=ZERO,
            total=ZERO,
            by_kind={key: ZERO for key in costs.KINDS},
            **mileage,
            **_stats(fuel[vehicle.id]),
        )
    for row in source['rows']:
        bucket = per_vehicle[row['vehicle_id']]
        key = 'total_' + row['payer']
        bucket[key] += row['amount']
        bucket['total'] += row['amount']
        bucket['by_kind'][row['kind'] if row['kind'] in costs.KINDS else 'other'] += (
            row['amount']
        )
        months[_month(row['date'])][key] += row['amount']
        months[_month(row['date'])]['total'] += row['amount']
    totals = {
        key: sum(
            (v[key] for v in per_vehicle.values()), ZERO if key != 'kilometers' else 0
        )
        for key in ('total_self', 'total_organization', 'total', 'kilometers')
    }
    totals.update(
        {flag: any(row[flag] for row in per_vehicle.values()) for flag in MILEAGE_FLAGS}
    )
    for bucket in [*per_vehicle.values(), totals, *months.values()]:
        bucket['cost_per_km'] = (
            _money(bucket['total'] / bucket['kilometers'])
            if bucket['kilometers']
            else None
        )
    return dict(
        period=dict(start=start, end=end),
        vehicles=list(per_vehicle.values()),
        totals=totals,
        monthly=list(months.values()),
        selected_ids=ids,
        available_vehicles=source['vehicles'],
        long_gap_days=LONG_GAP_DAYS,
    )


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
    return [
        (r['month'], r['cost_per_km'])
        for r in tco_report(user, [vehicle_id], _month(end, 1 - months), end)['monthly']
    ]


def report_csv(user, report):
    vehicles._user(user)
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(
        [
            'Автомобиль',
            'Личные, ₽',
            'Организация, ₽',
            'Всего, ₽',
            'Пробег, км',
            'Стоимость км, ₽',
            'Литры',
            'Расход, л/100 км',
            'Средняя цена литра, ₽',
            'Заправки',
            'Данные пробега',
        ]
    )

    def number(value, precision=2):
        return (
            '' if value is None else format(value, f'.{precision}f').replace('.', ',')
        )

    def mileage_status(row):
        if row.get('kind') == 'trailer':
            return 'Не применяется'
        status = 'Нет данных'
        if row['has_mileage']:
            status = 'Включает оценку' if row['estimated_mileage'] else 'По замерам'
        if row['odometer_decrease']:
            status += '. Пропущен интервал со снижением показаний'
        if row['long_mileage_gap']:
            status += f'. Интервал между замерами более {LONG_GAP_DAYS} дней'
        return status

    for row in report['vehicles']:
        name = row['name']
        if name.lstrip().startswith(('=', '+', '-', '@')):
            name = "'" + name
        writer.writerow(
            [
                name,
                *[
                    number(row[key])
                    for key in ('total_self', 'total_organization', 'total')
                ],
                row['kilometers'],
                number(row['cost_per_km']),
                number(row['total_liters'], 3),
                number(row['avg_consumption']),
                number(row['avg_price_per_liter']),
                row['refuelings_count'],
                mileage_status(row),
            ]
        )
    total = report['totals']
    writer.writerow(
        [
            'Итого',
            *[number(total[k]) for k in ('total_self', 'total_organization', 'total')],
            total['kilometers'],
            number(total['cost_per_km']),
            '',
            '',
            '',
            '',
            mileage_status(total),
        ]
    )
    record_activity('vehicle_tco_export', user, entity_type='vehicle_tco_export')
    return ('\ufeff' + output.getvalue()).encode('utf-8')
