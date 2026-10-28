"""Thin vehicle HTTP adapters; all ownership and validation live in services."""
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.models import RegistrationCountry, VehicleOdometerReading
from app.services import vehicle_service as service
from app.services.personal_access_service import can_use_personal_cabinet
from app.utils import is_local_test_user, superadmin_required
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
                       vehicle_fuels=service.FUELS, vehicle_statuses=service.STATUSES, archive_reasons=service.ARCHIVE_REASONS)
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
    def vehicle_detail(vehicle_id):
        vehicle = service.get_vehicle_for_user(current_user, vehicle_id)
        return render_template('vehicles/detail.html', vehicle=vehicle, mileage=service.current_mileage(vehicle),
            expenses=service.vehicle_expenses(current_user, vehicle_id), today=service.today(),
            readings=VehicleOdometerReading.query.filter_by(vehicle_id=vehicle.id).order_by(VehicleOdometerReading.reading_date.desc()).all())

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
