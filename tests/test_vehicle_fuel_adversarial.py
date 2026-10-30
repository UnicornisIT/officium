"""Independent regression probes for fuel transactions and shared history."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from werkzeug.datastructures import MultiDict

import test_vehicle_service
from app.models import Expense, VehicleCost, VehicleOdometerReading, VehicleRefueling, VehicleServiceRecord
from app.services import expense_service, vehicle_fuel_service as fuel, vehicle_service as vehicles, vehicle_maintenance_service as maintenance
from extensions import db


class FuelAdversarialTests(test_vehicle_service.VehicleServiceTestCase):
    def data(self, **updates):
        return dict(refuel_date='2026-03-01', odometer_km='1000', liters='40', price_per_liter='50', total_amount='2000', payer='self', is_full_tank=True) | updates

    def refuel(self, vehicle, **updates):
        return fuel.record_refueling(self.owner, vehicle.id, self.data(**updates))

    def edit(self, vehicle, row, **updates):
        return fuel.record_refueling(self.owner, vehicle.id, self.data(**updates), refueling_id=row.id)

    def test_move_fuel_owned_reading(self):
        vehicle = self.vehicle()
        row = self.refuel(vehicle)
        self.edit(vehicle, row, refuel_date='2026-03-02', odometer_km='1100')
        reading = VehicleOdometerReading.query.one()
        self.assertEqual((reading.reading_date, reading.odometer_km, reading.source), (date(2026, 3, 2), 1100, 'fuel'))
        self.assertEqual(Expense.query.one().expense_date, date(2026, 3, 2))

    def test_drop_fuel_owned_reading(self):
        vehicle = self.vehicle()
        row = self.refuel(vehicle)
        self.edit(vehicle, row, odometer_km='')
        self.assertEqual(VehicleOdometerReading.query.count(), 0)
        self.assertIsNone(row.odometer_km)

    def test_move_keeps_shared_manual_reading(self):
        vehicle = self.vehicle()
        original = vehicles.add_odometer_reading(self.owner, vehicle.id, '2026-03-01', 1000)
        row = self.refuel(vehicle)
        self.edit(vehicle, row, refuel_date='2026-03-02', odometer_km='1100')
        self.assertEqual(VehicleOdometerReading.query.count(), 2)
        self.assertEqual((original.reading_date, original.odometer_km, original.source), (date(2026, 3, 1), 1000, 'manual'))

    def test_move_keeps_shared_service_history(self):
        vehicle = self.vehicle()
        row = self.refuel(vehicle)
        service = maintenance.record_service(self.owner, vehicle.id, MultiDict(dict(service_date='2026-03-01', odometer_km='1000')))
        self.edit(vehicle, row, refuel_date='2026-03-02', odometer_km='1100')
        self.assertEqual(VehicleOdometerReading.query.count(), 2)
        self.assertEqual((service.service_date, service.odometer_km), (date(2026, 3, 1), 1000))
        old = VehicleOdometerReading.query.filter_by(reading_date=date(2026, 3, 1)).one()
        self.assertEqual(old.source, 'service')

    def test_shared_reading_change_rolls_back(self):
        vehicle = self.vehicle()
        row = self.refuel(vehicle)
        maintenance.record_service(self.owner, vehicle.id, MultiDict(dict(service_date='2026-03-01', odometer_km='1000')))
        with self.assertRaises(ValueError):
            self.edit(vehicle, row, odometer_km='1100', liters='41', total_amount='2050')
        self.assertEqual(row.odometer_km, 1000)
        self.assertEqual(Expense.query.one().amount, Decimal('2000'))
        self.assertEqual(VehicleOdometerReading.query.one().odometer_km, 1000)

    def test_error_after_reading_move_restores_all_records(self):
        vehicle = self.vehicle()
        row = self.refuel(vehicle)
        with patch('app.services.expense_service.update_expense', side_effect=ValueError('injected error')):
            with self.assertRaises(ValueError):
                self.edit(vehicle, row, refuel_date='2026-03-02', odometer_km='1100')
        reading = VehicleOdometerReading.query.one()
        self.assertEqual((row.refuel_date, reading.reading_date, reading.odometer_km), (date(2026, 3, 1), date(2026, 3, 1), 1000))
        self.assertEqual(Expense.query.one().amount, Decimal('2000'))

    def test_payer_switch_failure_restores_deleted_expense(self):
        vehicle = self.vehicle(is_company=True)
        row = self.refuel(vehicle)
        expense_id = row.expense_id
        with patch('app.services.vehicle_cost_service.create_vehicle_spend', side_effect=ValueError('injected error')):
            with self.assertRaises(ValueError):
                self.edit(vehicle, row, payer='organization', refuel_date='2026-03-02', odometer_km='1100')
        self.assertEqual(row.payer, 'self')
        self.assertEqual(row.expense_id, expense_id)
        self.assertIsNotNone(db.session.get(Expense, expense_id))
        self.assertEqual(VehicleCost.query.count(), 0)
        self.assertEqual(VehicleOdometerReading.query.one().reading_date, date(2026, 3, 1))

    def test_both_payer_switch_directions_keep_single_spend(self):
        vehicle = self.vehicle(is_company=True)
        row = self.refuel(vehicle)
        self.edit(vehicle, row, payer='organization')
        self.assertEqual((Expense.query.count(), VehicleCost.query.count()), (0, 1))
        self.edit(vehicle, row, payer='self')
        self.assertEqual((Expense.query.count(), VehicleCost.query.count()), (1, 0))
        self.assertEqual(Expense.query.one().amount, Decimal('2000'))
        self.assertEqual(VehicleRefueling.query.count(), 1)

    def test_paid_organization_cost_cannot_change_payer(self):
        vehicle = self.vehicle(is_company=True)
        row = self.refuel(vehicle, payer='organization')
        cost = db.session.get(VehicleCost, row.vehicle_cost_id)
        cost.paid_by_driver = True
        db.session.commit()
        with self.assertRaises(ValueError):
            self.edit(vehicle, row, refuel_date='2026-03-02', odometer_km='1100')
        self.assertEqual(row.payer, 'organization')
        self.assertEqual(VehicleOdometerReading.query.one().reading_date, date(2026, 3, 1))
        self.assertEqual((Expense.query.count(), VehicleCost.query.count()), (0, 1))

    def test_deleted_expense_can_be_recreated_on_edit(self):
        vehicle = self.vehicle()
        row = self.refuel(vehicle)
        expense_service.delete_expense(user_id=self.owner.id, expense_id=row.expense_id)
        self.edit(vehicle, row, liters='41', total_amount='2050')
        self.assertEqual(Expense.query.count(), 1)
        self.assertEqual(db.session.get(Expense, row.expense_id).amount, Decimal('2050'))

    def test_external_expense_vehicle_move_is_repaired_on_fuel_edit(self):
        vehicle = self.vehicle()
        row = self.refuel(vehicle)
        other = vehicles.create_vehicle(self.owner, dict(name='Другой автомобиль'))
        expense_service.update_expense(user_id=self.owner.id, expense_id=row.expense_id, amount='2000', title='Изменено', expense_date='2026-03-01', category='transport', vehicle_id=other.id)
        self.edit(vehicle, row)
        spend = db.session.get(Expense, row.expense_id)
        self.assertEqual((spend.vehicle_id, spend.vehicle_cost_kind), (vehicle.id, 'fuel'))

    def test_tolerance_boundary_and_localized_numbers(self):
        vehicle = self.vehicle()
        row = self.refuel(vehicle, liters='40,501', price_per_liter='52,30', total_amount='2 118,70')
        self.assertEqual(row.liters, Decimal('40.501'))
        self.assertEqual(row.total_amount, Decimal('2118.70'))
        with self.assertRaises(ValueError):
            self.refuel(vehicle, refuel_date='2026-03-02', liters='40,501', price_per_liter='52,30', total_amount='2118,71')
        self.assertEqual(VehicleRefueling.query.count(), 1)

    def test_empty_and_single_fill_period_stats(self):
        vehicle = self.vehicle()
        self.assertEqual(fuel.fuel_stats(vehicle, '2026-03-01', '2026-03-02')['refuelings_count'], 0)
        row = self.refuel(vehicle)
        stats = fuel.fuel_stats(vehicle, '2026-03-01', '2026-03-01')
        self.assertEqual(stats['total_liters'], 40)
        self.assertIsNone(stats['avg_consumption'])
        with self.assertRaises(ValueError): fuel.fuel_stats(vehicle, '2026-03-02', '2026-03-01')
    def test_deleted_expense_id_reuse_does_not_overwrite_unrelated_spending(self):
        vehicle = self.vehicle()
        row = self.refuel(vehicle)
        deleted_id = row.expense_id
        expense_service.delete_expense(user_id=self.owner.id, expense_id=deleted_id)
        unrelated = expense_service.create_expense(user_id=self.owner.id, amount='321', title='Покупка продуктов', category='products', expense_date='2026-03-05')
        self.assertEqual(unrelated.id, deleted_id)  # SQLite reuses INTEGER PRIMARY KEY without AUTOINCREMENT.
        self.edit(vehicle, row, liters='41', total_amount='2050')
        db.session.refresh(unrelated)
        self.assertEqual((unrelated.title, unrelated.amount, unrelated.vehicle_id), ('Покупка продуктов', Decimal('321'), None))
        self.assertNotEqual(row.expense_id, unrelated.id)

    def test_monthly_origin_switch_does_not_leave_dangling_children(self):
        vehicle = self.vehicle(is_company=True)
        row = self.refuel(vehicle, refuel_date='2026-01-01')
        expense_service.update_expense(user_id=self.owner.id, expense_id=row.expense_id, amount='2000', title='Заправка', category='transport', expense_date='2026-01-01', is_monthly=True)
        origin_id = row.expense_id
        self.assertGreater(Expense.query.filter_by(generated_from_id=origin_id).count(), 0)
        self.edit(vehicle, row, refuel_date='2026-01-01', payer='organization')
        for remaining in Expense.query.all():
            if remaining.generated_from_id is not None:
                self.assertIsNotNone(db.session.get(Expense, remaining.generated_from_id))
    def test_delete_commit_false_rolls_back_vehicle_links(self):
        vehicle = self.vehicle()
        row = self.refuel(vehicle)
        expense_id = row.expense_id
        service = VehicleServiceRecord(vehicle_id=vehicle.id, service_date=date(2026, 3, 1), odometer_km=1000, expense_id=expense_id)
        db.session.add(service)
        db.session.commit()
        expense_service.delete_expense(user_id=self.owner.id, expense_id=expense_id, commit=False)
        self.assertIsNone(row.expense_id)
        self.assertIsNone(service.expense_id)
        db.session.rollback()
        self.assertEqual(row.expense_id, expense_id)
        self.assertEqual(service.expense_id, expense_id)
        self.assertIsNotNone(db.session.get(Expense, expense_id))
        expense_service.delete_expense(user_id=self.owner.id, expense_id=expense_id)
        self.assertIsNone(row.expense_id)
        self.assertIsNone(service.expense_id)
