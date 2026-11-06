from decimal import Decimal

import test_vehicle_service
from app.services import vehicle_fuel_service as fuel


class FuelPartialConsumptionTests(test_vehicle_service.VehicleServiceTestCase):
    def refuel(self, vehicle, day, km, liters, full=True):
        return fuel.record_refueling(
            self.owner,
            vehicle.id,
            dict(
                refuel_date=f"2026-03-{day:02d}",
                odometer_km=km,
                liters=str(liters),
                price_per_liter="50",
                total_amount=str(liters * 50),
                is_full_tank=full,
            ),
        )

    def test_partial_liters_are_included_in_next_full_consumption(self):
        vehicle = self.vehicle()
        self.refuel(vehicle, 1, 10000, 40)
        partial = self.refuel(vehicle, 2, 10200, 10, full=False)
        last = self.refuel(vehicle, 3, 10600, 45)

        history = fuel.fuel_history(vehicle)
        self.assertEqual(history[-1]["consumption"], Decimal("9.17"))
        self.assertEqual(fuel.consumption(vehicle, last), Decimal("9.17"))
        self.assertEqual(
            fuel._stats([item["refueling"] for item in history])["avg_consumption"],
            history[-1]["consumption"],
        )
        self.assertIsNone(history[0]["consumption"])
        self.assertIsNone(history[1]["consumption"])
        self.assertIsNone(fuel.consumption(vehicle, partial))

    def test_multiple_partials_without_mileage_and_accumulator_reset(self):
        vehicle = self.vehicle()
        self.refuel(vehicle, 1, 10000, 40)
        self.refuel(vehicle, 2, "", 10, full=False)
        self.refuel(vehicle, 3, 10300, 5, full=False)
        self.refuel(vehicle, 4, 10600, 45)
        last = self.refuel(vehicle, 5, 11100, 40)

        history = fuel.fuel_history(vehicle)
        self.assertEqual(
            [item["consumption"] for item in history],
            [None, None, None, Decimal("10.00"), Decimal("8.00")],
        )
        self.assertEqual(fuel.consumption(vehicle, last), Decimal("8.00"))

    def test_initial_partial_does_not_supply_a_full_tank_baseline(self):
        vehicle = self.vehicle()
        self.refuel(vehicle, 1, 9800, 10, full=False)
        first_full = self.refuel(vehicle, 2, 10000, 40)
        last = self.refuel(vehicle, 3, 10500, 45)

        self.assertIsNone(fuel.consumption(vehicle, first_full))
        self.assertEqual(
            [item["consumption"] for item in fuel.fuel_history(vehicle)],
            [None, None, Decimal("9.00")],
        )
        self.assertEqual(fuel.consumption(vehicle, last), Decimal("9.00"))
