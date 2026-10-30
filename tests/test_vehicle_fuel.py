from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from werkzeug.exceptions import NotFound
import test_vehicle_service
from app.models import Expense, VehicleCost, VehicleRefueling, VehicleOdometerReading, ActivityLog
from app.services import vehicle_fuel_service as fuel, vehicle_service as vehicles


class VehicleFuelTests(test_vehicle_service.VehicleServiceTestCase):
    def record(self, vehicle, **values):
        data = dict(refuel_date='2026-03-01', liters='40.500', price_per_liter='52.30', total_amount='2118.15', odometer_km='1000') | values
        return fuel.record_refueling(self.owner, vehicle.id, data)

    def test_create_precision_tolerance_and_audit(self):
        vehicle = self.vehicle()
        row = self.record(vehicle, liters='40.501', total_amount='2118.20', station_name='Секретная АЗС')
        self.assertEqual(row.liters, Decimal('40.501'))
        self.assertEqual(Expense.query.one().amount, Decimal('2118.20'))
        self.assertEqual(VehicleOdometerReading.query.one().source, 'fuel')
        self.assertIsNone(fuel.consumption(vehicle, row))
        log = ActivityLog.query.filter_by(action='vehicle_refueling_create').one()
        self.assertEqual(log.entity_type, 'vehicle_refueling')
        self.assertFalse(log.description)
        self.record(vehicle, refuel_date='2026-03-02', total_amount='2118.55', odometer_km='1000')
        self.assertEqual(VehicleRefueling.query.count(), 2)
        with self.assertRaises(ValueError):
            self.record(vehicle, refuel_date='2026-03-03', total_amount='2118.75')
        self.assertEqual(VehicleRefueling.query.count(), 2)

    def test_invalid_values_and_transaction_failure(self):
        vehicle = self.vehicle()
        for values in [dict(liters='0'),dict(price_per_liter='0'),dict(total_amount='0'),dict(liters='NaN'),dict(liters='10000000'),dict(refuel_date=(vehicles.today()+timedelta(days=1)).isoformat()),dict(payer='organization'),dict(station_name='x'*151),dict(comment='x'*2001)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.record(vehicle, **values)
        with patch('app.services.expense_service.create_expense', side_effect=ValueError('failure')):
            with self.assertRaises(ValueError): self.record(vehicle)
        self.assertEqual(VehicleRefueling.query.count(),0)
        self.assertEqual(VehicleOdometerReading.query.count(),0)
        self.assertEqual(Expense.query.count(),0)

    def test_reading_conflict_duplicate_and_edit(self):
        vehicle = self.vehicle()
        vehicles.add_odometer_reading(self.owner,vehicle.id,'2026-03-01',1000)
        row = self.record(vehicle)
        with self.assertRaises(ValueError): self.record(vehicle)
        with self.assertRaises(ValueError): self.record(vehicle,refuel_date='2026-03-02',odometer_km='900')
        data = dict(refuel_date='2026-03-01',liters='50',price_per_liter='50',total_amount='2500',odometer_km='1100')
        with self.assertRaises(ValueError): fuel.record_refueling(self.owner,vehicle.id,data,refueling_id=row.id)
        self.assertEqual(row.total_amount,Decimal('2118.15'))
        data['odometer_km']='1000'
        fuel.record_refueling(self.owner,vehicle.id,data,refueling_id=row.id)
        self.assertEqual(Expense.query.count(),1)
        self.assertEqual(Expense.query.one().amount,Decimal('2500'))

    def test_payer_switch_without_double_counting(self):
        vehicle = self.vehicle(is_company=True)
        row = self.record(vehicle,payer='organization')
        self.assertEqual(Expense.query.count(),0)
        data=dict(refuel_date='2026-03-01',liters='40.5',price_per_liter='52.30',total_amount='2118.15',payer='self')
        fuel.record_refueling(self.owner,vehicle.id,data,refueling_id=row.id)
        self.assertEqual(VehicleCost.query.count(),0)
        self.assertEqual(Expense.query.count(),1)
        self.assertIsNone(row.vehicle_cost_id)
        self.assertIsNotNone(row.expense_id)

    def test_consumption_history_and_weighted_stats(self):
        vehicle = self.vehicle()
        a=self.record(vehicle,liters='40',price_per_liter='50',total_amount='2000')
        b=self.record(vehicle,refuel_date='2026-03-02',liters='50',price_per_liter='60',total_amount='3000',odometer_km='1500')
        self.assertEqual(fuel.consumption(vehicle,b),Decimal('10.00'))
        stats=fuel.fuel_stats(vehicle,date(2026,3,1),date(2026,3,2))
        self.assertEqual(stats['avg_price_per_liter'],Decimal('55.56'))
        self.assertEqual(stats['avg_consumption'],Decimal('10.00'))
        self.assertEqual(stats['total_liters'],90)
        a.is_full_tank=False
        self.assertIsNone(fuel.consumption(vehicle,b))
        a.is_full_tank=True
        b.odometer_km=1000
        self.assertIsNone(fuel.consumption(vehicle,b))
        self.assertEqual(len(fuel.fuel_history(vehicle)),2)

    def test_partial_fill_stats_and_no_mileage(self):
        vehicle = self.vehicle()
        self.record(vehicle)
        self.record(vehicle,refuel_date='2026-03-02',liters='10',price_per_liter='50',total_amount='500',odometer_km='',is_full_tank=False)
        last=self.record(vehicle,refuel_date='2026-03-03',liters='40',price_per_liter='50',total_amount='2000',odometer_km='1500')
        self.assertIsNone(fuel.consumption(vehicle,last))
        self.assertEqual(fuel.fuel_stats(vehicle,'2026-03-01','2026-03-03')['avg_consumption'],Decimal('10.00'))
        self.assertEqual(VehicleOdometerReading.query.count(),2)

    def test_owner_archive_trailer_electric_and_flag(self):
        vehicle = self.vehicle(fuel_type='electric')
        row=self.record(vehicle)
        with self.assertRaises(NotFound): fuel.record_refueling(self.other,vehicle.id,{})
        vehicles.archive_vehicle(self.owner,vehicle.id,'sold')
        with self.assertRaises(ValueError): self.record(vehicle,refuel_date='2026-03-02')
        trailer=vehicles.create_vehicle(self.owner,dict(name='Прицеп',kind='trailer'))
        self.login()
        self.assertNotIn('>Заправки<',self.client.get(f'/vehicles/{trailer.id}').get_data(as_text=True))
        self.assertEqual(self.client.get(f'/vehicles/{trailer.id}/refueling/new').status_code,404)
        self.login(self.other)
        self.assertEqual(self.client.get(f'/vehicles/{vehicle.id}/refueling/{row.id}').status_code,404)
        self.login()
        self.app.config['VEHICLES_ENABLED']=False
        for path in [f'/vehicles/{vehicle.id}/refueling/new', f'/vehicles/{vehicle.id}/refueling/{row.id}']:
            self.assertEqual(self.client.get(path).status_code,404)
            self.assertEqual(self.client.post(path).status_code,404)
        self.assertEqual(self.client.post(f'/vehicles/{vehicle.id}/refueling').status_code,404)

    def test_http_create_edit_period_and_checkbox(self):
        vehicle=self.vehicle()
        self.login()
        values=dict(refuel_date='2026-03-01',liters='40',price_per_liter='50',total_amount='2000',is_full_tank='0')
        response=self.client.post(f'/vehicles/{vehicle.id}/refueling',data=values)
        self.assertEqual(response.status_code,302)
        row=VehicleRefueling.query.one()
        self.assertFalse(row.is_full_tank)
        for url in [f'/vehicles/{vehicle.id}?tab=fuel', f'/vehicles/{vehicle.id}/refueling/{row.id}',f'/vehicles/{vehicle.id}?tab=fuel&date_from=2026-03-01&date_to=2026-03-02']:
            self.assertEqual(self.client.get(url).status_code,200)
        values.update(liters='50',total_amount='2500',is_full_tank='1')
        self.assertEqual(self.client.post(f'/vehicles/{vehicle.id}/refueling/{row.id}',data=values).status_code,302)
        self.assertTrue(row.is_full_tank)
        self.assertEqual(Expense.query.count(),1)
