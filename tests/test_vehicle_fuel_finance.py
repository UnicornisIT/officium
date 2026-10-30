"""Independent checks against real finance summaries, plans and history."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import test_vehicle_service
from app.models import Expense, VehicleCost, VehicleOdometerReading, VehicleRefueling
from app.services import vehicle_fuel_service as fuel
from app.services.finance_summary_service import get_finance_summary
from app.services.financial_plan_service import build_financial_plan
from app.utils import group_entries_by_month


class FuelFinanceIntegrationTests(test_vehicle_service.VehicleServiceTestCase):
    def values(self, **overrides):
        return dict(refuel_date='2026-03-01', liters='40.5', price_per_liter='52.30', total_amount='2118.15', odometer_km='1000') | overrides

    def test_payer_roundtrip_restores_all_personal_finance_views(self):
        vehicle = self.vehicle(is_company=True)
        before = get_finance_summary(self.owner.id, 2026, 3)
        plan = build_financial_plan(self.owner.id, today=date(2026, 3, 15))
        history = group_entries_by_month(Expense.query.all(), 'expense_date')
        row = fuel.record_refueling(self.owner, vehicle.id, self.values(payer='organization'))
        self.assertEqual(before, get_finance_summary(self.owner.id, 2026, 3))
        self.assertEqual(plan, build_financial_plan(self.owner.id, today=date(2026, 3, 15)))
        fuel.record_refueling(self.owner, vehicle.id, self.values(payer='self'), refueling_id=row.id)
        self.assertEqual(get_finance_summary(self.owner.id, 2026, 3)['total_expenses'], Decimal('2118.15'))
        self.assertEqual(VehicleCost.query.count(), 0)
        self.assertEqual(Expense.query.count(), 1)
        fuel.record_refueling(self.owner, vehicle.id, self.values(payer='organization'), refueling_id=row.id)
        self.assertEqual(before, get_finance_summary(self.owner.id, 2026, 3))
        self.assertEqual(plan, build_financial_plan(self.owner.id, today=date(2026, 3, 15)))
        self.assertEqual(history, group_entries_by_month(Expense.query.all(), 'expense_date'))
        self.assertEqual(VehicleCost.query.count(), 1)
        self.assertEqual(VehicleRefueling.query.count(), 1)

    def test_date_edit_moves_the_expense_between_financial_months(self):
        vehicle = self.vehicle()
        row = fuel.record_refueling(self.owner, vehicle.id, self.values())
        fuel.record_refueling(self.owner, vehicle.id, self.values(refuel_date='2026-02-28'), refueling_id=row.id)
        self.assertEqual(get_finance_summary(self.owner.id, 2026, 3)['total_expenses'], 0)
        self.assertEqual(get_finance_summary(self.owner.id, 2026, 2)['total_expenses'], Decimal('2118.15'))
        self.assertEqual(Expense.query.one().expense_date, date(2026, 2, 28))
        self.assertEqual(VehicleOdometerReading.query.one().reading_date, date(2026, 2, 28))
        self.assertEqual(fuel.fuel_stats(vehicle, '2026-03-01', '2026-03-31')['total_amount'], 0)

    def test_failure_after_all_mutations_rolls_back_every_financial_view(self):
        vehicle = self.vehicle(is_company=True)
        row = fuel.record_refueling(self.owner, vehicle.id, self.values())
        before = get_finance_summary(self.owner.id, 2026, 3)
        with patch('app.services.vehicle_fuel_service.record_activity', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                fuel.record_refueling(self.owner, vehicle.id, self.values(payer='organization', refuel_date='2026-03-02', odometer_km='1500'), refueling_id=row.id)
        self.assertEqual(before, get_finance_summary(self.owner.id, 2026, 3))
        self.assertEqual(row.payer, 'self')
        self.assertEqual(row.refuel_date, date(2026, 3, 1))
        self.assertEqual(VehicleCost.query.count(), 0)
        self.assertEqual(Expense.query.count(), 1)
        self.assertEqual(VehicleOdometerReading.query.one().reading_date, date(2026, 3, 1))
