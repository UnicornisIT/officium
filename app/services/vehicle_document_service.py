"""Owner-scoped vehicle documents and expiry dates."""
from datetime import date, timedelta
from app.models import Expense, Vehicle, VehicleDocument, VehicleCost
from app.services import vehicle_service as vehicles, vehicle_cost_service as costs, expense_service
from app.utils import parse_date, parse_decimal, record_activity
from extensions import db

KINDS = dict(osago='ОСАГО', kasko='КАСКО', tech_inspection='Техосмотр', pts='ПТС/ЭПТС', sts='СТС', license='Водительское удостоверение', other='Другое')
LABELS = dict(ok='В порядке', soon='Скоро истекает', overdue='Просрочено')
COLORS = dict(ok='success', soon='warning', overdue='danger')


def state(document, day):
    remaining = (document.expires_at - day).days if document.expires_at else None
    status = 'ok' if remaining is None or remaining > 14 else 'overdue' if remaining <= 0 else 'soon'
    return dict(status=status, remaining_days=remaining, label=LABELS[status], color=COLORS[status])


def documents_for(vehicle, *, day=None):
    day = day if day is not None else vehicles.today()
    rows = sorted(vehicle.documents, key=lambda d: (d.expires_at is None, d.expires_at or date.max, -(d.issued_date or date.min).toordinal(), d.id))
    return [(doc, state(doc, day)) for doc in rows]


def expiring_soon(user, days=14):
    vehicles._user(user)
    if not isinstance(days, int) or days < 0:
        raise ValueError('Укажите неотрицательное число дней.')
    day = vehicles.today()
    return VehicleDocument.query.join(Vehicle).filter(Vehicle.user_id == user.id, Vehicle.archived_at.is_(None), VehicleDocument.expires_at.between(day, day + timedelta(days=days))).order_by(VehicleDocument.expires_at, VehicleDocument.id).all()


def _linked_amount(user, vehicle, row, amount, action):
    if action not in ('', 'update', 'keep'):
        raise ValueError('Выберите действие для связанной траты.')
    if not (row.expense_id or row.vehicle_cost_id):
        return
    if row.expense_id and row.vehicle_cost_id:
        raise ValueError('Проверьте привязку траты к документу.')
    model, spend_id = (Expense, row.expense_id) if row.expense_id else (VehicleCost, row.vehicle_cost_id)
    spend = model.query.filter_by(id=spend_id, user_id=user.id, vehicle_id=vehicle.id).with_for_update().first_or_404()
    if amount == spend.amount or action == 'keep':
        return
    if not action:
        raise ValueError('Сумма отличается от связанной траты. Выберите: обновить трату или оставить её сумму прежней.')
    if amount is None:
        raise ValueError('Укажите положительную сумму для обновления связанной траты.')
    if isinstance(spend, Expense):
        if spend.is_monthly:
            raise ValueError('Ежемесячную трату измените в разделе расходов или выберите «Оставить сумму траты прежней».')
        expense_service.update_expense(user_id=user.id, expense_id=spend.id,
            amount=str(amount), category=spend.category, title=spend.title,
            expense_date=spend.expense_date.isoformat(), payment_method=spend.payment_method,
            comment=spend.comment, commit=False)
    else:
        spend.amount = amount
        record_activity('vehicle_cost_update', user, entity_type='vehicle_cost', entity_id=spend.id, commit=False)


def save_document(user, vehicle_id, data, document_id=None):
    try:
        vehicle = vehicles.get_vehicle_for_user(user, vehicle_id, for_update=True)
        vehicles._writable(vehicle)
        row = VehicleDocument.query.filter_by(id=document_id, vehicle_id=vehicle.id).first_or_404() if document_id else VehicleDocument(vehicle_id=vehicle.id)
        kind = data.get('kind')
        if kind not in KINDS:
            raise ValueError('Выберите тип документа.')
        duplicate = VehicleDocument.query.filter_by(vehicle_id=vehicle.id, kind=kind).first()
        if duplicate is not None and duplicate.id != row.id:
            raise ValueError('Документ этого типа уже добавлен. Отредактируйте существующий.')
        title, comment = str(data.get('title') or '').strip(), str(data.get('comment') or '').strip()
        if len(title) > 150 or len(comment) > 2000:
            raise ValueError('Название — до 150 символов, комментарий — до 2000 символов.')
        issued = parse_date(data.get('issued_date'), 'Дата выдачи')
        expires = parse_date(data.get('expires_at'), 'Дата истечения')
        if issued and expires and expires < issued:
            raise ValueError('Дата истечения не может быть раньше даты выдачи.')
        amount = parse_decimal(data.get('amount'), 'Сумма', required=False)
        if amount is not None and amount <= 0:
            raise ValueError('Сумма должна быть положительной.')
        _linked_amount(user, vehicle, row, amount, data.get('linked_amount_action', ''))
        row.kind, row.title, row.comment = kind, title, comment
        row.issued_date, row.expires_at, row.amount = issued, expires, amount
        db.session.add(row)
        db.session.flush()
        if data.get('create_expense') in ('on', '1', True):
            if amount is None:
                raise ValueError('Укажите сумму для создания траты.')
            if row.expense_id or row.vehicle_cost_id:
                raise ValueError('Трата уже связана с документом.')
            spend = costs.create_vehicle_spend(user, vehicle, data.get('payer'), amount=str(amount), cost_date=(issued or vehicles.today()).isoformat(), kind='insurance', commit=False)
            if isinstance(spend, Expense):
                row.expense_id = spend.id
            else:
                row.vehicle_cost_id = spend.id
        record_activity('vehicle_document_save', user, entity_type='vehicle_document', entity_id=row.id, commit=False)
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise
