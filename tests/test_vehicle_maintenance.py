from datetime import date, timedelta
from unittest.mock import patch
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import NotFound

import test_vehicle_service
from app.models import (ConsumableTemplate, VehicleMaintenanceItem, VehicleServiceRecord,
                        VehicleDefect, VehicleOdometerReading, VehicleCost, Expense, ActivityLog)
from app.services import vehicle_maintenance_service as maintenance, vehicle_service as vehicles
from extensions import db


class MaintenanceTests(test_vehicle_service.VehicleServiceTestCase):
    def test_account_delete_removes_maintenance_history(self):
        v = self.vehicle()
        item = self.item(v)
        maintenance.record_service(self.owner, v.id, self.service_data(item))
        maintenance.save_defect(self.owner, v.id, dict(title='Учебный дефект', severity='minor', found_date='2026-01-01'))
        db.session.delete(self.owner)
        db.session.commit()
        for model in [VehicleMaintenanceItem, VehicleServiceRecord, VehicleDefect, VehicleOdometerReading]:
            self.assertEqual(model.query.count(), 0)

    def item(self, vehicle, **values):
        return maintenance.save_item(self.owner, vehicle.id, dict(name='Учебное масло', interval_km='7000', last_done_km='12455', last_done_date='2026-01-01') | values)

    def service_data(self, item=None, **values):
        data = MultiDict(dict(service_date='2026-03-15', odometer_km='12455') | values)
        if item:
            data.add('item_ids', str(item.id))
        return data

    def template(self, **values):
        return maintenance.save_template(self.admin, dict(template_set='passenger', name='Учебное масло', interval_km='10000', interval_months='12', sort_order='0', is_active='on') | values)

    def test_status_boundaries_and_calendar(self):
        item = self.item(self.vehicle())
        for mileage, expected in [(18754, 'ok'), (18755, 'soon'), (19454, 'soon'), (19455, 'overdue'), (19456, 'overdue')]:
            self.assertEqual(maintenance.item_state(item, mileage, date(2026, 3, 15))['status'], expected)
        item.interval_months = 1
        item.last_done_date = date(2024, 1, 31)
        item.interval_km = None
        self.assertEqual(maintenance.add_months(item.last_done_date, 1), date(2024, 2, 29))
        for days, expected in [(31, 'ok'), (30, 'soon'), (1, 'soon'), (0, 'overdue'), (-1, 'overdue')]:
            self.assertEqual(maintenance.item_state(item, None, date(2024, 2, 29)-timedelta(days=days))['status'], expected)
        self.assertEqual(maintenance.add_months(date(2024, 2, 29), 12), date(2025, 2, 28))
        item.last_done_date = None
        self.assertEqual(maintenance.item_state(item, None, date.today())['status'], 'unknown')

    def test_earliest_deadline_and_forecast(self):
        v = self.vehicle()
        item = self.item(v, interval_months='12')
        self.assertEqual(maintenance.item_state(item, 0, date(2027, 1, 1))['status'], 'overdue')
        for day, km in [('2026-01-01', 1000), ('2026-01-30', 2000)]:
            vehicles.add_odometer_reading(self.owner, v.id, day, km)
        self.assertIsNone(maintenance.mileage_forecast(v, 3000))
        vehicles.add_odometer_reading(self.owner, v.id, '2026-01-31', 2500)
        db.session.expire(v, ['readings'])
        self.assertEqual(maintenance.mileage_forecast(v, 4000), date(2026, 3, 2))
        self.assertIsNone(maintenance.mileage_forecast(v, 2500))

    def test_template_copy_electric_truck_and_trailer(self):
        template = self.template()
        v = self.vehicle()
        item = maintenance.save_item(self.owner, v.id, dict(template_id=str(template.id)))
        self.template(name='Учебный фильтр', applies_to_electric='on')
        maintenance.save_template(self.admin, dict(template_set='passenger', name='Изменено', interval_km='20000', interval_months='24', is_active='on'), template.id)
        self.assertEqual(item.interval_km, 10000)
        self.assertEqual(item.name, 'Учебное масло')
        electric = vehicles.create_vehicle(self.owner, dict(name='Учебный электромобиль', fuel_type='electric'))
        self.assertNotIn(template.id, [t.id for t in maintenance.templates_for(electric)])
        with self.assertRaises(NotFound):
            maintenance.save_item(self.owner, electric.id, dict(template_id=template.id))
        trailer = vehicles.create_vehicle(self.owner, dict(name='Учебный прицеп', kind='trailer'))
        trailer_item = maintenance.save_item(self.owner, trailer.id, dict(template_id=template.id))
        self.assertIsNone(trailer_item.interval_km)
        self.assertEqual(trailer_item.interval_months, 24)
        with self.assertRaises(ValueError):
            self.item(trailer)
        self.template(template_set='truck', name='Грузовое масло', interval_km='30000')
        truck = vehicles.create_vehicle(self.owner, dict(name='Учебный грузовик', kind='truck'))
        self.assertEqual([t.name for t in maintenance.templates_for(truck)], ['Грузовое масло'])

    def test_atomic_service_expense_and_defect(self):
        v = self.vehicle()
        item = self.item(v)
        defect = maintenance.save_defect(self.owner, v.id, dict(title='Учебная поломка', severity='critical', found_date='2026-03-01', set_in_repair='on'))
        self.assertEqual(v.status, 'in_repair')
        self.assertEqual(maintenance.technical_condition(v), 'overdue')
        data = self.service_data(item, amount='125.50', payer='self', comment='Учебная замена')
        data.add('defect_ids', str(defect.id))
        row = maintenance.record_service(self.owner, v.id, data)
        self.assertEqual(row.expense_id, Expense.query.one().id)
        self.assertEqual(defect.status, 'fixed')
        self.assertEqual(defect.service_record_id, row.id)
        self.assertEqual(item.last_done_km, 12455)
        self.assertEqual(maintenance.item_state(item, 12455, date(2026, 3, 15))['due_km'], 19455)
        self.assertEqual(VehicleOdometerReading.query.one().source, 'service')
        self.assertFalse(vehicles.needs_mileage_update(v, vehicles.today()))
        self.assertEqual(maintenance.technical_condition(v), 'ok')
        for log in ActivityLog.query.filter(ActivityLog.action.like('vehicle_%')).all():
            self.assertIsNone(log.description)

    def test_rollback_after_spend_flush(self):
        v = self.vehicle(is_company=True)
        item = self.item(v)
        defect = maintenance.save_defect(self.owner, v.id, dict(title='Учебный дефект', severity='critical', found_date='2026-01-01'))
        for payer in ['self', 'organization']:
            data = self.service_data(item, amount='12.34', payer=payer)
            data.add('defect_ids', str(defect.id))
            real_audit = maintenance._audit
            def fail(user, action, row, commit=True):
                if action == 'vehicle_service_create':
                    db.session.flush()
                    raise RuntimeError('simulated post-spend failure')
                return real_audit(user, action, row, commit)
            with patch.object(maintenance, '_audit', side_effect=fail), self.assertRaises(RuntimeError):
                maintenance.record_service(self.owner, v.id, data)
            self.assertEqual(VehicleServiceRecord.query.count(), 0)
            self.assertEqual(VehicleOdometerReading.query.count(), 0)
            self.assertEqual(Expense.query.count(), 0)
            self.assertEqual(VehicleCost.query.count(), 0)
            self.assertEqual(item.last_done_date, date(2026, 1, 1))
            self.assertEqual(defect.status, 'open')
            self.assertIsNone(v.mileage_snooze_until)
        row = maintenance.record_service(self.owner, v.id, self.service_data(item, amount='12.34', payer='organization'))
        self.assertEqual(row.vehicle_cost_id, VehicleCost.query.one().id)
        self.assertEqual(Expense.query.count(), 0)

    def test_condition_filter_and_minor_defects(self):
        v = self.vehicle()
        defect = maintenance.save_defect(self.owner, v.id, dict(title='Учебный дефект', severity='minor', found_date='2026-01-01'))
        self.assertEqual(maintenance.technical_condition(v), 'ok')
        defect.severity = 'attention'
        db.session.commit()
        self.assertEqual(maintenance.technical_condition(v), 'soon')
        self.assertEqual(v.status, 'active')
        self.login()
        response = self.client.get('/vehicles?attention=1')
        self.assertIn(v.name.encode(), response.data)
        defect.status, defect.fixed_date = 'fixed', date(2026, 1, 2)
        db.session.commit()
        self.assertNotIn('vehicle-card p-3', self.client.get('/vehicles?attention=1').get_data(as_text=True))

    def test_idor_feature_flag_demo_and_archive(self):
        own = self.vehicle()
        item = self.item(own)
        foreign = vehicles.create_vehicle(self.other, dict(name='Чужой учебный автомобиль'))
        foreign_item = maintenance.save_item(self.other, foreign.id, dict(name='Чужая позиция', interval_months='1'))
        defect = maintenance.save_defect(self.other, foreign.id, dict(title='Чужой дефект', severity='minor', found_date='2026-01-01'))
        self.login()
        for url in [f'/vehicles/{foreign.id}?tab=maintenance', f'/vehicles/{foreign.id}/service']:
            self.assertEqual(self.client.get(url).status_code, 404)
        for path, data in [(f'{foreign.id}/maintenance', dict(name='X', interval_months='1')),
                           (f'{own.id}/maintenance/{foreign_item.id}', dict(name='X', interval_months='1')),
                           (f'{own.id}/defects/{defect.id}', dict(title='X', severity='minor', found_date='2026-01-01')),
                           (f'{own.id}/service', self.service_data(foreign_item)),
                           (f'{own.id}/service', self.service_data(defect_ids=str(defect.id)))]:
            self.assertEqual(self.client.post('/vehicles/'+path, data=data).status_code, 404)
        vehicles.archive_vehicle(self.owner, own.id, 'sold')
        self.assertEqual(self.client.get(f'/vehicles/{own.id}?tab=maintenance').status_code, 200)
        with self.assertRaises(ValueError):
            maintenance.record_service(self.owner, own.id, self.service_data(item))
        with self.assertRaises(ValueError):
            self.item(own)
        self.app.config['VEHICLES_ENABLED'] = False
        for path in ['/vehicles', f'/vehicles/{own.id}?tab=defects', f'/vehicles/{own.id}/service', '/admin/consumable-templates', '/admin/consumable-templates/1']:
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.post(path).status_code, 404)

    def test_admin_privacy_and_csrf(self):
        vehicle = self.vehicle()
        item = self.item(vehicle)
        self.login(self.admin)
        template = self.template()
        response = self.client.get('/admin/consumable-templates')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(vehicle.name.encode(), response.data)
        self.assertEqual(self.client.get(f'/vehicles/{vehicle.id}').status_code, 404)
        self.assertEqual(self.client.post(f'/vehicles/{vehicle.id}/service', data=self.service_data(item)).status_code, 404)
        self.login()
        self.assertEqual(self.client.get('/admin/consumable-templates').status_code, 302)
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(self.client.post(f'/vehicles/{vehicle.id}/service', data=self.service_data(item)).status_code, 400)

    def test_validation_and_same_day_reading(self):
        v = self.vehicle()
        item = self.item(v)
        with self.assertRaises(ValueError):
            vehicles.update_vehicle(self.owner, v.id, dict(kind='trailer'))
        for data in [dict(name='X'), dict(name='X', interval_km='0'), dict(name='X', interval_km='-1'), dict(name='X', interval_months='1201')]:
            with self.assertRaises(ValueError):
                maintenance.save_item(self.owner, v.id, data)
        vehicles.add_odometer_reading(self.owner, v.id, '2026-03-15', '12455')
        maintenance.record_service(self.owner, v.id, self.service_data(item))
        self.assertEqual(VehicleOdometerReading.query.count(), 1)
        with self.assertRaises(ValueError):
            maintenance.record_service(self.owner, v.id, self.service_data(odometer_km='12500'))
        with self.assertRaises(ValueError):
            maintenance.record_service(self.owner, v.id, MultiDict(dict(service_date='9999-01-01', odometer_km='12500')))
