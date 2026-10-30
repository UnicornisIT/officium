"""Independent orchestration checks for fuel access, schema and lifecycle."""
import re
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import test_vehicle_service
from app.models import ActivityLog, Expense, User, Vehicle, VehicleCost, VehicleRefueling
from app.services import vehicle_fuel_service as fuel, vehicle_service as vehicles
from extensions import db


class VehicleFuelSecurityTests(test_vehicle_service.VehicleServiceTestCase):
    def values(self, **overrides):
        return dict(refuel_date='2026-03-01', liters='40', price_per_liter='50',
                    total_amount='2000', odometer_km='1000', payer='self') | overrides

    def record(self, vehicle, **overrides):
        return fuel.record_refueling(self.owner, vehicle.id, self.values(**overrides))

    def test_cross_vehicle_refueling_id_cannot_read_or_write(self):
        first = self.vehicle()
        second = vehicles.create_vehicle(self.owner, dict(name='Second vehicle'))
        row = self.record(second)
        self.login()
        url = f'/vehicles/{first.id}/refueling/{row.id}'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, data=self.values(total_amount='2500', liters='50')).status_code, 404)
        self.assertEqual(row.total_amount, Decimal('2000'))
        self.assertEqual(Expense.query.count(), 1)

    def test_foreign_owner_all_fuel_routes_and_periods_are_private(self):
        vehicle = self.vehicle()
        row = self.record(vehicle)
        self.login(self.other)
        paths = [f'/vehicles/{vehicle.id}?tab=fuel',
                 f'/vehicles/{vehicle.id}?tab=fuel&date_from=2026-03-01',
                 f'/vehicles/{vehicle.id}/refueling/new',
                 f'/vehicles/{vehicle.id}/refueling/{row.id}']
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        for path in [f'/vehicles/{vehicle.id}/refueling', f'/vehicles/{vehicle.id}/refueling/{row.id}']:
            self.assertEqual(self.client.post(path, data=self.values()).status_code, 404)
        self.assertEqual(VehicleRefueling.query.count(), 1)

    def test_csrf_required_for_create_and_edit_and_valid_token_accepted(self):
        vehicle = self.vehicle()
        self.login()
        self.app.config['WTF_CSRF_ENABLED'] = True
        create_url = f'/vehicles/{vehicle.id}/refueling'
        self.assertEqual(self.client.post(create_url, data=self.values()).status_code, 400)
        html = self.client.get(f'/vehicles/{vehicle.id}/refueling/new').get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        self.assertEqual(self.client.post(create_url, data=self.values(csrf_token=token)).status_code, 302)
        row = VehicleRefueling.query.one()
        self.assertEqual(self.client.post(f'{create_url}/{row.id}', data=self.values()).status_code, 400)
        self.assertEqual(row.total_amount, Decimal('2000'))

    def test_disabled_feature_and_unauthenticated_requests(self):
        vehicle = self.vehicle()
        row = self.record(vehicle)
        for path in [f'/vehicles/{vehicle.id}/refueling/new', f'/vehicles/{vehicle.id}/refueling/{row.id}']:
            self.assertEqual(self.client.get(path).status_code, 302)
        self.login()
        self.app.config['VEHICLES_ENABLED'] = False
        for path in [f'/vehicles/{vehicle.id}/refueling/new', f'/vehicles/{vehicle.id}/refueling/{row.id}']:
            self.assertEqual(self.client.get(path).status_code, 404)
        for path in [f'/vehicles/{vehicle.id}/refueling', f'/vehicles/{vehicle.id}/refueling/{row.id}']:
            self.assertEqual(self.client.post(path, data=self.values()).status_code, 404)

    def test_archive_rejects_edit_and_preserves_history(self):
        vehicle = self.vehicle()
        row = self.record(vehicle)
        vehicles.archive_vehicle(self.owner, vehicle.id, 'sold')
        self.login()
        response = self.client.post(f'/vehicles/{vehicle.id}/refueling/{row.id}', data=self.values(liters='50', total_amount='2500'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(row.total_amount, Decimal('2000'))
        self.assertEqual(Expense.query.one().amount, Decimal('2000'))
        self.assertNotIn('name="liters"', response.get_data(as_text=True))
        history = self.client.get(f'/vehicles/{vehicle.id}?tab=fuel').get_data(as_text=True)
        self.assertIn('40.000', history)
        self.assertNotIn('>Добавить заправку<', history)

    def test_station_and_comment_escape_and_audit_remains_generic(self):
        vehicle = self.vehicle()
        station = '<script>alert("secret-station")</script>'
        comment = '</textarea><script>alert("secret-comment")</script>'
        row = self.record(vehicle, station_name=station, comment=comment)
        self.login()
        for path in [f'/vehicles/{vehicle.id}?tab=fuel', f'/vehicles/{vehicle.id}/refueling/{row.id}']:
            html = self.client.get(path).get_data(as_text=True)
            self.assertNotIn(station, html)
            self.assertNotIn(comment, html)
        for log in ActivityLog.query.all():
            self.assertNotIn('secret-', log.description or '')
            self.assertNotIn('2000', log.description or '')
        log = ActivityLog.query.filter_by(action='vehicle_refueling_create').one()
        self.assertEqual(log.entity_type, 'vehicle_refueling')
        self.assertEqual(log.entity_id, row.id)
        self.assertFalse(log.description)

    def test_database_constraints_and_unique_date_with_equal_mileage_allowed(self):
        vehicle = self.vehicle()
        self.record(vehicle)
        self.record(vehicle, refuel_date='2026-03-02')
        for overrides in [dict(liters=0), dict(price_per_liter=0), dict(total_amount=0),
                          dict(odometer_km=-1), dict(payer='other'), dict(refuel_date='2026-03-01')]:
            with self.subTest(overrides=overrides):
                values = self.values(refuel_date='2026-03-03') | overrides
                from datetime import date
                values['refuel_date'] = date.fromisoformat(values['refuel_date'])
                row = VehicleRefueling(vehicle_id=vehicle.id, **values)
                with self.assertRaises(IntegrityError):
                    with db.session.begin_nested():
                        db.session.add(row)
                        db.session.flush()
        self.assertEqual(VehicleRefueling.query.count(), 2)

    def test_expense_and_cost_deletion_nulls_links_with_foreign_keys_enabled(self):
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        self.assertEqual(db.session.execute(text('PRAGMA foreign_keys')).scalar(), 1)
        vehicle = self.vehicle(is_company=True)
        personal = self.record(vehicle)
        company = self.record(vehicle, refuel_date='2026-03-02', payer='organization')
        db.session.delete(db.session.get(Expense, personal.expense_id))
        db.session.delete(db.session.get(VehicleCost, company.vehicle_cost_id))
        db.session.commit()
        db.session.expire_all()
        self.assertIsNone(personal.expense_id)
        self.assertIsNone(company.vehicle_cost_id)
        self.assertEqual(VehicleRefueling.query.count(), 2)

    def test_account_orm_deletion_cascades_fuel_without_touching_other_owner(self):
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        self.assertEqual(db.session.execute(text('PRAGMA foreign_keys')).scalar(), 1)
        vehicle = self.vehicle(is_company=True)
        self.record(vehicle)
        self.record(vehicle, refuel_date='2026-03-02', payer='organization')
        other_id = self.other.id
        db.session.delete(self.owner)
        db.session.commit()
        self.assertEqual(VehicleRefueling.query.count(), 0)
        self.assertEqual(Expense.query.count(), 0)
        self.assertEqual(VehicleCost.query.count(), 0)
        self.assertIsNotNone(db.session.get(User, other_id))

    def test_deleted_expense_id_reuse_does_not_overwrite_unrelated_spend(self):
        from app.services import expense_service
        vehicle = self.vehicle()
        row = self.record(vehicle)
        previous_id = row.expense_id
        expense_service.delete_expense(user_id=self.owner.id, expense_id=previous_id)
        self.assertIsNone(row.expense_id)
        unrelated = expense_service.create_expense(user_id=self.owner.id, amount='125',
            title='Unrelated expense', category='other', expense_date='2026-03-03')
        self.assertEqual(unrelated.id, previous_id, 'SQLite fixture must exercise reused ID')
        fuel.record_refueling(self.owner, vehicle.id, self.values(liters='50', total_amount='2500'), refueling_id=row.id)
        self.assertEqual(unrelated.amount, Decimal('125'), 'Editing fuel must not overwrite an unrelated expense')
        self.assertEqual(unrelated.title, 'Unrelated expense')
        self.assertIsNone(unrelated.vehicle_id)


    def test_other_owner_expense_id_reuse_survives_payer_switch(self):
        from app.services import expense_service
        from app.services.finance_summary_service import get_finance_summary
        vehicle = self.vehicle(is_company=True)
        row = self.record(vehicle)
        previous_id = row.expense_id
        expense_service.delete_expense(user_id=self.owner.id, expense_id=previous_id)
        unrelated = expense_service.create_expense(user_id=self.other.id, amount='125',
            title='Other owner expense', category='other', expense_date='2026-03-03')
        unrelated_id = unrelated.id
        self.assertEqual(unrelated_id, previous_id)
        before = get_finance_summary(self.other.id, 2026, 3)
        fuel.record_refueling(self.owner, vehicle.id, self.values(payer='organization'), refueling_id=row.id)
        db.session.expire_all()
        surviving = db.session.get(Expense, unrelated_id)
        self.assertIsNotNone(surviving, 'Payer switch must not delete another owner expense')
        self.assertEqual(surviving.user_id, self.other.id)
        self.assertEqual(surviving.amount, Decimal('125'))
        cost = VehicleCost.query.one()
        self.assertEqual(cost.user_id, self.owner.id)
        self.assertEqual(row.vehicle_cost_id, cost.id)
        self.assertIsNone(row.expense_id)
        self.assertEqual(get_finance_summary(self.other.id, 2026, 3), before)
