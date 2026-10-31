"""Thin vehicle HTTP adapters; all ownership and validation live in services."""
from flask import Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.models import RegistrationCountry, VehicleOdometerReading, ConsumableTemplate
from app.services import vehicle_document_service as documents
from app.services import vehicle_fuel_service as fuel
from app.services import vehicle_maintenance_service as maintenance
from app.services import vehicle_service as service
from app.services import vehicle_cost_service as costs
from app.services.personal_access_service import can_use_personal_cabinet
from app.utils import EXPENSE_CATEGORIES, PAYMENT_METHODS, is_local_test_user, superadmin_required
from extensions import db


def _attempt(operation):
    try:
        return operation()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    except SQLAlchemyError:
        db.session.rollback()
        # Driver exception messages may include VIN, plate or vehicle name.
        current_app.logger.error('Vehicle operation failed; database details withheld')
        flash('Не удалось сохранить данные автомобиля. Повторите попытку.', 'danger')


def init_app(app):
    @app.context_processor
    def vehicle_context():
        enabled = bool(app.config.get('VEHICLES_ENABLED'))
        personal = enabled and can_use_personal_cabinet(current_user) and not is_local_test_user() and not request.path.startswith('/admin')
        context = dict(vehicles_enabled=enabled, vehicle_kinds=service.KINDS, vehicle_bodies=service.BODIES,
                       vehicle_fuels=service.FUELS, vehicle_statuses=service.STATUSES, archive_reasons=service.ARCHIVE_REASONS, vehicle_payment_methods=dict(PAYMENT_METHODS), vehicle_expense_categories=dict(EXPENSE_CATEGORIES), technical_condition=maintenance.technical_condition, maintenance_labels=maintenance.LABELS, maintenance_colors=maintenance.COLORS)
        if personal and request.endpoint in ('index', 'vehicles'):
            context['mileage_due'] = service.due_vehicles(current_user)
        if personal and request.endpoint in ('expenses', 'edit_expense'):
            context['expense_vehicles'] = service.selectable_vehicles(current_user)
        return context

    @app.route('/vehicles')
    @login_required
    def vehicles():
        service.require_enabled()
        if is_local_test_user():
            return render_template('vehicles/list.html', pagination=None, is_demo=True)
        return render_template('vehicles/list.html', pagination=service.list_vehicles(current_user, request.args))

    @app.route('/vehicles/new', methods=['GET', 'POST'])
    @login_required
    def new_vehicle():
        service._user(current_user)
        if request.method == 'POST':
            vehicle = _attempt(lambda: service.create_with_trailer(current_user, request.form))
            if vehicle:
                return redirect(url_for('vehicle_detail', vehicle_id=vehicle.id))
        return _form(None)

    def _form(vehicle):
        return render_template('vehicles/form.html', vehicle=vehicle, form=request.form if request.method == 'POST' else {},
            **service.form_options(current_user))

    @app.route('/vehicles/<int:vehicle_id>')
    @login_required
    def vehicle_detail(vehicle_id, *, document_form=None, error_document_id=None):
        vehicle = service.get_vehicle_for_user(current_user, vehicle_id)
        fuel_detail = _attempt(lambda: fuel.detail(vehicle, request.args.get('date_from'), request.args.get('date_to')))
        if fuel_detail is None:
            return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id, tab='fuel'))
        return render_template('vehicles/detail.html', vehicle=vehicle, mileage=service.current_mileage(vehicle),
            documents=documents.documents_for(vehicle), document_kinds=documents.KINDS,
            document_form=document_form, error_document_id=error_document_id,
            vehicle_tab='documents' if document_form is not None else request.args.get('tab', 'overview'),
            expenses=service.vehicle_expenses(current_user, vehicle_id), today=service.today(),
            report=costs.vehicle_report(current_user, vehicle_ids=[vehicle_id]),
            organization_costs=costs.vehicle_cost_rows(current_user, vehicle_id) if request.args.get('tab') == 'expenses' else [], cost_kinds=costs.KINDS,
            readings=VehicleOdometerReading.query.filter_by(vehicle_id=vehicle.id).order_by(VehicleOdometerReading.reading_date.desc()).all(), **maintenance.detail(vehicle), **fuel_detail)

    @app.route('/vehicles/<int:vehicle_id>/refueling/new')
    @app.route('/vehicles/<int:vehicle_id>/refueling', methods=['POST'])
    @app.route('/vehicles/<int:vehicle_id>/refueling/<int:refueling_id>', methods=['GET', 'POST'])
    @login_required
    def vehicle_refueling(vehicle_id, refueling_id=None):
        vehicle = service.get_vehicle_for_user(current_user, vehicle_id)
        if vehicle.kind == 'trailer':
            abort(404)
        row = fuel.get_refueling(vehicle, refueling_id) if refueling_id else None
        if request.method == 'POST':
            result = _attempt(lambda: fuel.record_refueling(current_user, vehicle_id, request.form, refueling_id=refueling_id))
            if result:
                return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id, tab='fuel'))
        values = request.form if request.method == 'POST' else ({column.name: getattr(row, column.name) for column in row.__table__.columns} if row else {})
        return render_template('vehicles/refueling_form.html', vehicle=vehicle, refueling=row, form=values, today=service.today())

    @app.route('/vehicles/<int:vehicle_id>/documents', methods=['POST'])
    @app.route('/vehicles/<int:vehicle_id>/documents/<int:document_id>', methods=['POST'])
    @login_required
    def vehicle_document(vehicle_id, document_id=None):
        service.require_enabled()
        result = _attempt(lambda: documents.save_document(current_user, vehicle_id, request.form, document_id))
        if result is None:
            return vehicle_detail(vehicle_id, document_form=request.form, error_document_id=document_id), 422
        if request.form.get('linked_amount_action') == 'keep':
            flash('Документ сохранён. Сумма связанной траты оставлена прежней.', 'success')
        return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id, tab='documents'))

    @app.route('/vehicles/<int:vehicle_id>/maintenance', methods=['POST'])
    @login_required
    def vehicle_maintenance(vehicle_id):
        _attempt(lambda: maintenance.save_item(current_user, vehicle_id, request.form))
        return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id, tab='maintenance'))

    @app.route('/vehicles/<int:vehicle_id>/maintenance/<int:item_id>', methods=['POST'])
    @login_required
    def vehicle_maintenance_edit(vehicle_id, item_id):
        _attempt(lambda: maintenance.save_item(current_user, vehicle_id, request.form, item_id))
        return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id, tab='maintenance'))

    @app.route('/vehicles/<int:vehicle_id>/service', methods=['GET', 'POST'])
    @login_required
    def vehicle_record_service(vehicle_id):
        vehicle = service.get_vehicle_for_user(current_user, vehicle_id)
        if request.method == 'POST':
            result = _attempt(lambda: maintenance.record_service(current_user, vehicle_id, request.form))
            if result:
                return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id, tab='maintenance'))
        return render_template('vehicles/service_form.html', vehicle=vehicle, today=service.today(), form=request.form,
                               mileage=service.current_mileage(vehicle), **maintenance.detail(vehicle))

    @app.route('/vehicles/<int:vehicle_id>/defects', methods=['POST'])
    @login_required
    def vehicle_defect(vehicle_id):
        _attempt(lambda: maintenance.save_defect(current_user, vehicle_id, request.form))
        return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id, tab='defects'))

    @app.route('/vehicles/<int:vehicle_id>/defects/<int:defect_id>', methods=['POST'])
    @login_required
    def vehicle_defect_edit(vehicle_id, defect_id):
        _attempt(lambda: maintenance.save_defect(current_user, vehicle_id, request.form, defect_id))
        return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id, tab='defects'))

    @app.route('/admin/consumable-templates', methods=['GET', 'POST'])
    @superadmin_required
    def admin_consumable_templates():
        service.require_enabled()
        if request.method == 'POST':
            _attempt(lambda: maintenance.save_template(current_user, request.form))
        return render_template('vehicles/consumable_templates.html', templates=ConsumableTemplate.query.order_by(ConsumableTemplate.template_set, ConsumableTemplate.sort_order, ConsumableTemplate.id).all())

    @app.route('/admin/consumable-templates/<int:template_id>', methods=['POST'])
    @superadmin_required
    def admin_consumable_template_edit(template_id):
        _attempt(lambda: maintenance.save_template(current_user, request.form, template_id))
        return redirect(url_for('admin_consumable_templates'))

    @app.route('/vehicles/<int:vehicle_id>/edit', methods=['GET', 'POST'])
    @login_required
    def edit_vehicle(vehicle_id):
        vehicle = service.get_vehicle_for_user(current_user, vehicle_id)
        if request.method == 'POST':
            updated = _attempt(lambda: service.update_vehicle(current_user, vehicle_id, dict(request.form, is_company=request.form.get('is_company', ''))))
            if updated:
                return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id))
        elif vehicle.archived_at:
            flash('Автомобиль в архиве: сначала восстановите его.', 'warning')
            return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id))
        return _form(vehicle)

    @app.route('/vehicles/<int:vehicle_id>/archive', methods=['POST'])
    @login_required
    def archive_vehicle(vehicle_id):
        _attempt(lambda: service.archive_vehicle(current_user, vehicle_id, request.form.get('reason'), request.form.get('note', '')))
        return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id))

    @app.route('/vehicles/<int:vehicle_id>/restore', methods=['POST'])
    @login_required
    def restore_vehicle(vehicle_id):
        _attempt(lambda: service.restore_vehicle(current_user, vehicle_id))
        return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id))

    @app.route('/vehicles/<int:vehicle_id>/odometer', methods=['POST'])
    @login_required
    def vehicle_odometer(vehicle_id):
        _attempt(lambda: service.add_odometer_reading(current_user, vehicle_id, request.form.get('reading_date'), request.form.get('odometer_km')))
        return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id, tab='mileage'))

    @app.route('/vehicles/mileage', methods=['POST'])
    @login_required
    def vehicle_mileage_batch():
        service._user(current_user)
        _attempt(lambda: service.update_mileage_batch(current_user, request.form))
        return redirect(url_for('vehicles'))

    @app.route('/vehicles/<int:vehicle_id>/expense')
    @login_required
    def vehicle_expense(vehicle_id):
        vehicle = service.get_vehicle_for_user(current_user, vehicle_id)
        if vehicle.archived_at or vehicle.status != 'active':
            flash('Расходы можно добавлять только на активный автомобиль вне архива.', 'warning')
            return redirect(url_for('vehicle_detail', vehicle_id=vehicle_id))
        return redirect(url_for('expenses', vehicle_id=vehicle.id))

    @app.route('/vehicles/<int:vehicle_id>/spend', methods=['GET', 'POST'])
    @login_required
    def vehicle_spend(vehicle_id):
        vehicle = service.get_vehicle_for_user(current_user, vehicle_id)
        if request.method == 'POST':
            result = _attempt(lambda: costs.create_vehicle_spend(current_user, vehicle, request.form.get('payer'),
                amount=request.form.get('amount'), cost_date=request.form.get('cost_date'),
                kind=request.form.get('kind'), comment=request.form.get('comment'),
                paid_by_driver=request.form.get('paid_by_driver'), title=request.form.get('title'), payment_method=request.form.get('payment_method')))
            if result:
                return redirect(url_for('vehicle_reports', vehicle_id=vehicle.id))
        return render_template('vehicles/spend.html', vehicle=vehicle, kinds=costs.KINDS, today=service.today())

    @app.route('/vehicles/reports')
    @login_required
    def vehicle_reports():
        service._user(current_user)
        report = _attempt(lambda: costs.vehicle_report(current_user, request.args.get('date_from'), request.args.get('date_to'),
            request.args.getlist('vehicle_id'), unpaid=request.args.get('unpaid') == '1', selected_day=request.args.get('day')))
        if report is None:
            return redirect(url_for('vehicle_reports'))
        if request.args.get('format') == 'csv':
            return Response(costs.report_csv(current_user, report), content_type='text/csv; charset=utf-8',
                headers={'Content-Disposition': 'attachment; filename=vehicle-report.csv'})
        return render_template('vehicles/report.html', report=report, kinds=costs.KINDS, today=service.today())

    @app.route('/vehicles/costs/<int:cost_id>')
    @login_required
    def vehicle_cost_detail(cost_id):
        cost = costs.get_cost_for_user(current_user, cost_id)
        vehicle = service.get_vehicle_for_user(current_user, cost.vehicle_id)
        return render_template('vehicles/cost_detail.html', cost=costs._cost_row(cost), vehicle=vehicle,
            kinds=costs.KINDS, today=service.today())

    @app.route('/vehicles/costs/<int:cost_id>/driver-payment', methods=['POST'])
    @login_required
    def vehicle_driver_payment(cost_id):
        result = _attempt(lambda: costs.mark_paid_by_driver(current_user, cost_id))
        if result and request.form.get('return_to') == 'vehicle':
            return redirect(url_for('vehicle_detail', vehicle_id=result.vehicle_id, tab='expenses'))
        return redirect(url_for('vehicle_reports'))

    @app.route('/vehicles/costs/<int:cost_id>/reimburse', methods=['POST'])
    @login_required
    def vehicle_reimburse(cost_id):
        result = _attempt(lambda: costs.mark_reimbursed(current_user, cost_id, request.form.get('date')))
        if result and request.form.get('return_to') == 'vehicle':
            return redirect(url_for('vehicle_detail', vehicle_id=result.vehicle_id, tab='expenses'))
        return redirect(url_for('vehicle_reports'))

    @app.route('/admin/registration-countries', methods=['GET', 'POST'])
    @superadmin_required
    def admin_registration_countries():
        service.require_enabled()
        if request.method == 'POST':
            _attempt(lambda: service.save_country(current_user, request.form))
        return render_template('vehicles/countries.html', countries=RegistrationCountry.query.order_by(RegistrationCountry.sort_order).all())

    @app.route('/admin/registration-countries/<int:country_id>', methods=['POST'])
    @superadmin_required
    def admin_update_registration_country(country_id):
        _attempt(lambda: service.save_country(current_user, request.form, country_id))
        return redirect(url_for('admin_registration_countries'))
