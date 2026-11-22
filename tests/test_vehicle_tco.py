from datetime import date
from decimal import Decimal
from time import perf_counter
import unittest
import uuid
from unittest.mock import patch
import test_vehicle_service
from app.models import (
    VehicleDocument,
    VehicleRefueling,
    VehicleOdometerReading,
    ActivityLog,
)
from app.services import vehicle_tco_service as tco, vehicle_cost_service as costs
from extensions import db


class VehicleTCOTests(unittest.TestCase):
    setUp = test_vehicle_service.VehicleServiceTestCase.setUp
    tearDown = test_vehicle_service.VehicleServiceTestCase.tearDown
    login = test_vehicle_service.VehicleServiceTestCase.login

    def vehicle(self, **values):
        from app.services import vehicle_service

        return vehicle_service.create_vehicle(
            self.owner, dict(name=f'TCO {uuid.uuid4()}', **values)
        )

    def report(self, *ids):
        return tco.compare_vehicles(self.owner, ids or None, '2026-01-01', '2026-12-31')

    def add_readings(self, vehicle, samples):
        db.session.add_all(
            [
                VehicleOdometerReading(
                    vehicle_id=vehicle.id,
                    reading_date=date.fromisoformat(day),
                    odometer_km=km,
                    source='manual',
                )
                for day, km in samples
            ]
        )
        db.session.commit()

    def test_monthly_mileage_reconciles_with_quarter(self):
        car = self.vehicle()
        self.add_readings(car, [('2026-09-30', 1000), ('2026-10-05', 1100)])
        costs.create_vehicle_spend(
            self.owner, car, amount='10.10', cost_date='2026-09-30'
        )
        costs.create_vehicle_spend(
            self.owner, car, amount='20.00', cost_date='2026-10-01'
        )

        report = tco.tco_report(self.owner, [car.id], '2026-09-01', '2026-11-30')
        self.assertEqual(report['totals']['kilometers'], 100)
        self.assertEqual(sum(row['kilometers'] for row in report['monthly']), 100)
        self.assertEqual([row['kilometers'] for row in report['monthly']], [20, 80, 0])
        self.assertEqual(
            [row['estimated_mileage'] for row in report['monthly']], [True, True, False]
        )
        self.assertFalse(report['totals']['estimated_mileage'])
        self.assertEqual(report['totals']['cost_per_km'], Decimal('0.30'))
        self.assertEqual(
            [row['cost_per_km'] for row in report['monthly']],
            [Decimal('0.51'), Decimal('0.25'), None],
        )

        self.login()
        url = (
            f'/vehicles/tco?vehicle_id={car.id}&date_from=2026-09-01&date_to=2026-11-30'
        )
        page = self.client.get(url).get_data(as_text=True)
        self.assertIn('100 км (по замерам)', page)
        self.assertIn('20 км (включает оценку)', page)
        self.assertIn('80 км (включает оценку)', page)
        csv = self.client.get(url + '&format=csv').data.decode('utf-8-sig')
        self.assertIn('По замерам', csv)
        csv = self.client.get(
            f'/vehicles/tco?vehicle_id={car.id}&date_from=2026-09-01&date_to=2026-09-30&format=csv'
        ).data.decode('utf-8-sig')
        self.assertIn(';20;0,51;', csv)
        self.assertIn('Включает оценку', csv)

    def test_mileage_uses_nearest_boundary_readings_and_rounds_consistently(self):
        car = self.vehicle()
        self.add_readings(
            car,
            [
                ('2026-09-01', 0),
                ('2026-09-29', 10),
                ('2026-10-03', 15),
                ('2026-10-31', 25),
            ],
        )
        periods = [
            ('2026-09-29', '2026-10-02'),
            ('2026-09-29', '2026-09-30'),
            ('2026-10-01', '2026-10-02'),
            ('2026-09-30', '2026-09-30'),
        ]
        reports = [tco.tco_report(self.owner, [car.id], *period) for period in periods]

        whole, september, october, single_day = [report['totals'] for report in reports]
        self.assertEqual([row['kilometers'] for row in reports[0]['monthly']], [3, 2])
        self.assertEqual(
            whole['kilometers'], september['kilometers'] + october['kilometers']
        )
        self.assertEqual(whole['kilometers'], 5)
        self.assertFalse(whole['estimated_mileage'])
        self.assertTrue(september['estimated_mileage'])
        self.assertTrue(october['estimated_mileage'])
        self.assertEqual(single_day['kilometers'], 2)

    def test_mileage_reset_long_gap_stationary_and_unavailable_readings(self):
        car, stationary, single, empty, trailer = [
            self.vehicle(kind=kind) for kind in ['passenger'] * 4 + ['trailer']
        ]
        self.add_readings(
            car,
            [
                ('2026-01-01', 1000),
                ('2026-01-11', 1100),
                ('2026-02-01', 10),
                ('2026-06-01', 130),
            ],
        )
        self.add_readings(stationary, [('2026-01-01', 500), ('2026-01-11', 500)])
        self.add_readings(single, [('2026-01-01', 500)])
        self.add_readings(trailer, [('2026-01-01', 0), ('2026-01-11', 1000)])
        costs.create_vehicle_spend(
            self.owner, car, amount='440.10', cost_date='2026-01-01'
        )
        report = self.report(car.id, stationary.id, single.id, empty.id, trailer.id)
        rows = {row['id']: row for row in report['vehicles']}
        self.assertEqual(rows[car.id]['kilometers'], 220)
        self.assertEqual(rows[car.id]['cost_per_km'], Decimal('2.00'))
        self.assertTrue(rows[car.id]['odometer_decrease'])
        self.assertTrue(rows[car.id]['long_mileage_gap'])
        self.assertFalse(rows[car.id]['estimated_mileage'])
        self.assertEqual(
            [row['kilometers'] for row in report['monthly'][:6]],
            [100, 28, 31, 30, 31, 0],
        )
        self.assertTrue(report['monthly'][0]['odometer_decrease'])
        self.assertTrue(report['monthly'][1]['long_mileage_gap'])
        self.assertTrue(report['monthly'][1]['estimated_mileage'])
        self.assertTrue(rows[stationary.id]['has_mileage'])
        self.assertEqual(rows[stationary.id]['kilometers'], 0)
        self.assertIsNone(rows[stationary.id]['cost_per_km'])
        for vehicle in (single, empty, trailer):
            self.assertFalse(rows[vehicle.id]['has_mileage'])
            self.assertEqual(rows[vehicle.id]['kilometers'], 0)
            self.assertIsNone(rows[vehicle.id]['cost_per_km'])
        self.assertEqual(report['totals']['kilometers'], 220)
        self.assertEqual(
            [
                row.odometer_km
                for row in VehicleOdometerReading.query.filter_by(
                    vehicle_id=car.id
                ).order_by(VehicleOdometerReading.reading_date)
            ],
            [1000, 1100, 10, 130],
        )

    def test_totals_categories_comparison_and_mileage(self):
        a = self.vehicle(is_company=True)
        b = self.vehicle()
        costs.create_vehicle_spend(
            self.owner, a, amount='6000', cost_date='2026-01-01', kind='fuel'
        )
        costs.create_vehicle_spend(
            self.owner,
            a,
            'organization',
            amount='4000',
            cost_date='2026-01-02',
            kind='service',
        )
        for day, km in [(1, 1000), (20, 2000)]:
            db.session.add(
                VehicleOdometerReading(
                    vehicle_id=a.id,
                    reading_date=date(2026, 1, day),
                    odometer_km=km,
                    source='manual',
                )
            )
        db.session.commit()
        report = self.report(a.id, b.id)
        self.assertEqual(report['vehicles'][0]['cost_per_km'], Decimal('10.00'))
        self.assertEqual(report['vehicles'][0]['by_kind']['fuel'], 6000)
        self.assertEqual(report['totals']['total'], 10000)
        self.assertEqual(report['totals']['total_organization'], 4000)
        self.assertEqual(report['totals']['kilometers'], 1000)
        self.assertEqual(report['monthly'][0]['cost_per_km'], Decimal('10.00'))
        self.assertIsNone(report['vehicles'][1]['cost_per_km'])
        self.assertEqual(report['vehicles'][1]['total'], 0)
        self.assertEqual(len(tco.cost_per_km_history(self.owner, a.id, 3)), 3)

    def test_fuel_documents_and_no_double_counting(self):
        v = self.vehicle()
        for day, km, liters in [(1, 1000, 40), (2, 1500, 50)]:
            spend = costs.create_vehicle_spend(
                self.owner,
                v,
                amount=str(liters * 50),
                cost_date=f'2026-01-0{day}',
                kind='fuel',
            )
            db.session.add(
                VehicleRefueling(
                    vehicle_id=v.id,
                    refuel_date=date(2026, 1, day),
                    odometer_km=km,
                    liters=liters,
                    price_per_liter=50,
                    total_amount=liters * 50,
                    payer='self',
                    is_full_tank=True,
                    expense_id=spend.id,
                )
            )
        spend = costs.create_vehicle_spend(
            self.owner, v, amount='1000', cost_date='2026-01-01', kind='insurance'
        )
        db.session.add_all(
            [
                VehicleDocument(
                    vehicle_id=v.id,
                    kind='osago',
                    issued_date=date(2026, 1, 1),
                    amount=2000,
                    expense_id=spend.id,
                ),
                VehicleDocument(
                    vehicle_id=v.id,
                    kind='kasko',
                    issued_date=date(2026, 1, 1),
                    amount=3000,
                ),
                VehicleDocument(vehicle_id=v.id, kind='other', amount=999),
            ]
        )
        db.session.commit()
        row = self.report(v.id)['vehicles'][0]
        self.assertEqual(row['total'], 8500)
        self.assertEqual(row['by_kind']['insurance'], 4000)
        self.assertEqual(row['total_liters'], 90)
        self.assertEqual(row['avg_consumption'], Decimal('10.00'))
        self.assertEqual(row['avg_price_per_liter'], 50)
        self.assertEqual(row['refuelings_count'], 2)

    def test_forecast(self):
        clock = patch(
            'app.services.vehicle_service.today', return_value=date(2026, 7, 15)
        )
        clock.start()
        self.addCleanup(clock.stop)
        for amounts, expected in [
            ([10000] * 6, Decimal('10000')),
            ([10000, 12000, 14000], Decimal('15600')),
            ([10000, 100000], None),
            ([10000] * 5 + [100000], None),
            ([10000], None),
            ([10000, 0, 10000], None),
        ]:
            v = self.vehicle()
            for i, amount in enumerate(amounts):
                if amount:
                    costs.create_vehicle_spend(
                        self.owner,
                        v,
                        amount=str(amount),
                        cost_date=date(2026, 7 - len(amounts) + i, 1),
                    )
            self.assertEqual(tco.forecast_next_month(self.owner, v.id), expected)

    def test_routes_privacy_csv_flag_and_invalid_filters(self):
        v = self.vehicle(vin='X' * 17)
        v.name = '=SECRET'
        db.session.commit()
        costs.create_vehicle_spend(
            self.owner, v, amount='123.45', cost_date='2026-01-01'
        )
        self.login()
        self.assertEqual(self.client.get('/vehicles/tco').status_code, 200)
        self.assertEqual(self.client.get(f'/vehicles/{v.id}?tab=tco').status_code, 200)
        response = self.client.get('/vehicles/tco?format=csv')
        self.assertEqual(
            response.headers['Content-Disposition'],
            'attachment; filename=vehicle-tco.csv',
        )
        text = response.data.decode('utf-8-sig')
        self.assertIn("'=SECRET", text)
        self.assertIn('123,45', text)
        self.assertNotIn('X' * 17, text)
        log = ActivityLog.query.filter_by(action='vehicle_tco_export').one()
        self.assertEqual(log.entity_type, 'vehicle_tco_export')
        self.assertEqual(log.user_id, self.owner.id)
        self.assertIsNone(log.entity_id)
        self.assertFalse(log.description)
        for value in ['bogus', '0', str(v.id + 100)]:
            self.assertEqual(
                self.client.get('/vehicles/tco?vehicle_id=' + value).status_code, 404
            )
        self.assertEqual(
            self.client.get('/vehicles/tco?date_from=bad').status_code, 302
        )
        self.assertEqual(
            self.client.get(
                '/vehicles/tco?date_from=2026-02-01&date_to=2026-01-01'
            ).status_code,
            302,
        )
        self.login(self.other)
        self.assertEqual(
            self.client.get(f'/vehicles/tco?vehicle_id={v.id}').status_code, 404
        )
        self.assertEqual(self.client.get(f'/vehicles/{v.id}?tab=tco').status_code, 404)
        self.login()
        self.app.config['VEHICLES_ENABLED'] = False
        self.assertEqual(self.client.get('/vehicles/tco').status_code, 404)

    def test_trailer_empty_period_and_zero_spending_with_mileage(self):
        v = self.vehicle(kind='trailer')
        costs.create_vehicle_spend(self.owner, v, amount='500', cost_date='2026-01-01')
        row = self.report(v.id)['vehicles'][0]
        self.assertEqual(row['total'], 500)
        self.assertIsNone(row['cost_per_km'])
        self.login()
        response = self.client.get(f'/vehicles/{v.id}?tab=tco')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="tco-distance"', response.get_data(as_text=True))
        car = self.vehicle()
        for day, km in [(1, 0), (20, 1000)]:
            db.session.add(
                VehicleOdometerReading(
                    vehicle_id=car.id,
                    reading_date=date(2026, 1, day),
                    odometer_km=km,
                    source='manual',
                )
            )
        db.session.commit()
        self.assertEqual(
            self.report(car.id)['vehicles'][0]['cost_per_km'], Decimal('0.00')
        )
        empty = tco.tco_report(self.other, [], date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(empty['vehicles'], [])
        self.assertEqual(empty['totals']['total'], 0)

    def test_batched_performance(self):
        from sqlalchemy import event
        from app.models import Vehicle, Expense

        for i in range(50):
            v = Vehicle(
                user_id=self.owner.id,
                name=f'Performance {i}',
                kind='passenger',
                body_type='sedan',
                fuel_type='petrol',
            )
            db.session.add(v)
            db.session.flush()
            for month in range(1, 13):
                db.session.add(
                    Expense(
                        user_id=self.owner.id,
                        vehicle_id=v.id,
                        amount=10000,
                        title='Fixture',
                        category='transport',
                        expense_date=date(2026, month, 1),
                        vehicle_cost_kind='fuel',
                    )
                )
                for day, km in [(1, month * 1000), (28, month * 1000 + 500)]:
                    db.session.add(
                        VehicleOdometerReading(
                            vehicle_id=v.id,
                            reading_date=date(2026, month, day),
                            odometer_km=km,
                            source='manual',
                        )
                    )
        db.session.commit()
        ten_ids = [v.id for v in Vehicle.query.limit(10).all()]
        started = perf_counter()
        tco.tco_report(self.owner, ten_ids, date(2026, 1, 1), date(2026, 12, 31))
        print(f'TCO 10 vehicles / 12 months: {perf_counter() - started:.3f}s')
        queries = []

        def count(*args):
            queries.append(1)

        event.listen(db.engine, 'before_cursor_execute', count)
        try:
            started = perf_counter()
            report = self.report()
            elapsed = perf_counter() - started
        finally:
            event.remove(db.engine, 'before_cursor_execute', count)
        print(
            f'TCO 50 vehicles / 12 months: {elapsed:.3f}s, {len(queries)} SQL queries'
        )
        self.assertEqual(report['totals']['total'], 6000000)
        self.assertLess(len(queries), 15)
