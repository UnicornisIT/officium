"""Owner-scoped spending, reporting and CSV; organization costs never create Expense."""
import calendar
import csv
import io
from datetime import date
from decimal import Decimal
from flask import abort
from app.models import Expense, Vehicle, VehicleCost
from app.services import vehicle_service as vehicles, expense_service
from app.utils import PAYMENT_METHODS, parse_date, parse_decimal, record_activity
from extensions import db

KINDS = dict(fuel='Топливо', service='Обслуживание', parts='Запчасти', insurance='Страхование', tax='Налог', fines='Штрафы', parking='Парковка', wash='Мойка', other='Другое')


def create_vehicle_spend(user, vehicle, payer=None, *, amount, cost_date, kind='other', comment='', paid_by_driver=False, title=None, payment_method=None, is_monthly=False):
    vehicle = vehicles.get_vehicle_for_user(user, vehicle.id, for_update=True)
    vehicles._writable(vehicle)
    payer = payer or vehicle.default_payer
    if payer not in ('self', 'organization') or (payer == 'organization' and not vehicle.is_company):
        raise ValueError('Организация может оплачивать только служебное авто.')
    if kind not in KINDS:
        raise ValueError('Выберите вид расхода.')
    amount = parse_decimal(amount, 'Сумма', required=True)
    if not amount.is_finite() or amount <= 0 or amount > Decimal('9999999999.99') or amount != amount.quantize(Decimal('.01')):
        raise ValueError('Введите положительную сумму с точностью до копеек.')
    day = parse_date(cost_date, 'Дата', required=True)
    comment = str(comment or '').strip()
    if len(comment) > 2000:
        raise ValueError('Комментарий — до 2000 символов.')
    title = KINDS[kind] if title is None else str(title).strip()
    if not title or len(title) > 150:
        raise ValueError('Название расхода обязательно, не более 150 символов.')
    payment_method = str(payment_method or '').strip() or None
    if payment_method and payment_method not in dict(PAYMENT_METHODS):
        raise ValueError('Выберите корректный способ оплаты.')
    if payer == 'self':
        return expense_service.create_expense(user_id=user.id, vehicle_id=vehicle.id, amount=amount,
            expense_date=day.isoformat(), title=title or KINDS[kind], vehicle_cost_kind=kind, category='transport', comment=comment, payment_method=payment_method, is_monthly=is_monthly)
    if is_monthly:
        raise ValueError('Траты организации не поддерживают ежемесячное повторение.')
    cost = VehicleCost(user_id=user.id, vehicle_id=vehicle.id, amount=amount, cost_date=day,
        kind=kind, title=title, payment_method=payment_method, comment=comment, paid_by_driver=paid_by_driver is True or paid_by_driver == 'on')
    db.session.add(cost)
    db.session.flush()
    record_activity('vehicle_cost_create', user, entity_type='vehicle_cost', entity_id=cost.id, commit=False)
    db.session.commit()
    return cost


def get_cost_for_user(user, cost_id):
    vehicles._user(user)
    cost = VehicleCost.query.filter_by(id=cost_id, user_id=user.id).first_or_404()
    vehicles.get_vehicle_for_user(user, cost.vehicle_id)
    return cost


def mark_paid_by_driver(user, cost_id):
    vehicles._user(user)
    cost = VehicleCost.query.filter_by(id=cost_id, user_id=user.id).with_for_update().first_or_404()
    vehicle = vehicles.get_vehicle_for_user(user, cost.vehicle_id, for_update=True)
    vehicles._writable(vehicle)
    cost.paid_by_driver = True
    record_activity('vehicle_cost_driver_payment', user, entity_type='vehicle_cost', entity_id=cost.id, commit=False)
    db.session.commit()
    return cost


def _cost_row(item):
    return dict(id=item.id, vehicle_id=item.vehicle_id, date=item.cost_date, amount=item.amount,
        payer='organization', title=item.title or 'Без названия', payment_method=item.payment_method, kind=item.kind, comment=item.comment or '', paid_by_driver=item.paid_by_driver,
        unpaid=item.paid_by_driver and not item.reimbursed_at, reimbursed_at=item.reimbursed_at)


def vehicle_cost_rows(user, vehicle_id):
    vehicle = vehicles.get_vehicle_for_user(user, vehicle_id)
    return [_cost_row(item) for item in VehicleCost.query.filter_by(user_id=user.id, vehicle_id=vehicle.id)
        .order_by(VehicleCost.cost_date.desc(), VehicleCost.id.desc()).all()]


def mark_reimbursed(user, cost_id, reimbursement_date=None):
    vehicles._user(user)
    cost = VehicleCost.query.filter_by(id=cost_id, user_id=user.id).with_for_update().first_or_404()
    vehicle = vehicles.get_vehicle_for_user(user, cost.vehicle_id, for_update=True)
    vehicles._writable(vehicle)
    day = parse_date(reimbursement_date, 'Дата возмещения', required=True) if reimbursement_date else None
    if day and (not cost.paid_by_driver or day < cost.cost_date or day > vehicles.today()):
        raise ValueError('Проверьте дату и отметку оплаты водителем.')
    cost.reimbursed_at = day
    record_activity('vehicle_cost_reimburse', user, entity_type='vehicle_cost', entity_id=cost.id, commit=False)
    db.session.commit()
    return cost


def vehicle_report(user, date_from=None, date_to=None, vehicle_ids=None, *, unpaid=False, selected_day=None):
    vehicles._user(user)
    today = vehicles.today()
    start = parse_date(date_from, 'Начало', required=True) if date_from else today.replace(day=1)
    end = parse_date(date_to, 'Конец', required=True) if date_to else date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    if end < start:
        raise ValueError('Конец периода раньше начала.')
    owned = Vehicle.query.filter_by(user_id=user.id).order_by(Vehicle.name).all()
    ids = {v.id for v in owned}
    if vehicle_ids:
        try:
            selected = {int(key) for key in vehicle_ids}
        except (TypeError, ValueError):
            abort(404)
        if not selected <= ids:
            abort(404)
        ids = selected
    rows = []
    for item in Expense.query.filter(Expense.user_id == user.id, Expense.vehicle_id.in_(ids), Expense.expense_date.between(start, end)).all():
        kind = item.vehicle_cost_kind or 'other'
        rows.append(dict(id=item.id, vehicle_id=item.vehicle_id, date=item.expense_date, amount=item.amount, payer='self', title=item.title, payment_method=item.payment_method, kind=kind, comment=item.comment or '', unpaid=False, reimbursed_at=None))
    for item in VehicleCost.query.filter(VehicleCost.user_id == user.id, VehicleCost.vehicle_id.in_(ids), VehicleCost.cost_date.between(start, end)).all():
        rows.append(_cost_row(item))
    rows.sort(key=lambda row: (row['date'], row['id']))
    if unpaid:
        rows = [row for row in rows if row['unpaid']]
    totals = dict(self=Decimal(0), organization=Decimal(0), total=Decimal(0), unpaid=Decimal(0))
    per_vehicle = {v.id: dict(totals) for v in owned if v.id in ids}
    kinds = {key: dict(totals) for key in KINDS}
    days = {}
    for row in rows:
        days.setdefault(row['date'], set()).add(row['payer'])
        for bucket in (totals, per_vehicle[row['vehicle_id']], kinds[row['kind']]):
            bucket[row['payer']] += row['amount']
            bucket['total'] += row['amount']
            if row['unpaid']:
                bucket['unpaid'] += row['amount']
    day = parse_date(selected_day, 'День', required=True) if selected_day else None
    return dict(start=start, end=end, rows=rows, totals=totals, per_vehicle=per_vehicle, kinds=kinds,
        vehicles=owned, selected_ids=ids, days=days, calendar=calendar.Calendar().monthdatescalendar(start.year,start.month),
        selected_day=day, day_rows=[r for r in rows if r['date'] == day], unpaid=unpaid)


def report_csv(user, report):
    vehicles._user(user)
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Дата', 'Автомобиль', 'Плательщик', 'Вид', 'Название', 'Способ оплаты', 'Сумма', 'К возмещению', 'Комментарий'])
    names = {v.id: v.name for v in report['vehicles']}
    def safe(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
    for row in report['rows']:
        writer.writerow([row['date'].isoformat(), safe(names[row['vehicle_id']]), 'Я' if row['payer']=='self' else 'Организация', KINDS[row['kind']], safe(row['title']), dict(PAYMENT_METHODS).get(row['payment_method'], ''), format(row['amount'], '.2f').replace('.', ','), 'Да' if row['unpaid'] else 'Нет', safe(row['comment'])])
    writer.writerow(['Итого', '', '', '', '', '', format(report['totals']['total'], '.2f').replace('.', ','), '', ''])
    record_activity('vehicle_report_export', user)
    return ('\ufeff' + output.getvalue()).encode('utf-8')
