"""Independent checks against real finance summaries, plans and history."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import test_vehicle_service
from app.models import Expense, VehicleCost, VehicleOdometerReading, VehicleRefueling
from app.services import vehicle_fuel_service as fuel
from app.services import vehicle_cost_service as costs
from app.services.finance_summary_service import get_finance_summary
from app.services.financial_plan_service import build_financial_plan
from app.utils import group_entries_by_month


class FuelFinanceIntegrationTests(test_vehicle_service.VehicleServiceTestCase):
    def values(self, **overrides):
        return dict(refuel_date='2026-03-01', liters='40.5', price_per_liter='52.30', total_amount='2118.15', odometer_km='1000') | overrides

    def reimbursed_refueling(self):
        vehicle = self.vehicle(is_company=True)
        row = fuel.record_refueling(self.owner, vehicle.id, self.values(payer='organization'))
        costs.mark_paid_by_driver(self.owner, row.vehicle_cost_id)
        costs.mark_reimbursed(self.owner, row.vehicle_cost_id, '2026-03-05')
        return vehicle, row

    def test_reimbursed_refueling_rejects_amount_change(self):
        vehicle, row = self.reimbursed_refueling()
        with self.assertRaisesRegex(ValueError, 'Трата уже возмещена: изменить сумму или дату нельзя'):
            fuel.record_refueling(self.owner, vehicle.id,
                self.values(payer='organization', liters='41', total_amount='2144.30'), refueling_id=row.id)
        self.assertEqual(row.total_amount, Decimal('2118.15'))
        self.assertEqual(row.liters, Decimal('40.5'))
        self.assertEqual(VehicleCost.query.one().amount, Decimal('2118.15'))

    def test_reimbursed_refueling_rejects_date_change(self):
        vehicle, row = self.reimbursed_refueling()
        for day in ('2026-02-28', '2026-03-03', '2026-03-06'):
            with self.subTest(day=day):
                with self.assertRaisesRegex(ValueError, 'Трата уже возмещена: изменить сумму или дату нельзя'):
                    fuel.record_refueling(self.owner, vehicle.id,
                        self.values(payer='organization', refuel_date=day), refueling_id=row.id)
                self.assertEqual(row.refuel_date, date(2026, 3, 1))
                self.assertEqual(VehicleCost.query.one().cost_date, date(2026, 3, 1))
                self.assertEqual(VehicleOdometerReading.query.one().reading_date, date(2026, 3, 1))

    def test_reimbursed_refueling_allows_other_field_changes(self):
        vehicle, row = self.reimbursed_refueling()
        fuel.record_refueling(self.owner, vehicle.id,
            self.values(payer='organization', station_name='Новая АЗС', comment='Уточнение',
                liters='42.363', price_per_liter='50.00'), refueling_id=row.id)
        cost = VehicleCost.query.one()
        self.assertEqual((row.station_name, row.comment), ('Новая АЗС', 'Уточнение'))
        self.assertEqual((cost.title, cost.comment), ('Заправка Новая АЗС', 'Уточнение'))
        self.assertEqual((row.liters, row.price_per_liter), (Decimal('42.363'), Decimal('50.00')))
        self.assertEqual((cost.amount, cost.cost_date), (Decimal('2118.15'), date(2026, 3, 1)))
        self.assertEqual(cost.reimbursed_at, date(2026, 3, 5))

    def test_refueling_amount_edit_allowed_after_reimbursement_removed(self):
        vehicle, row = self.reimbursed_refueling()
        costs.mark_reimbursed(self.owner, row.vehicle_cost_id)
        fuel.record_refueling(self.owner, vehicle.id,
            self.values(payer='organization', liters='41', total_amount='2144.30'), refueling_id=row.id)
        self.assertEqual(row.total_amount, Decimal('2144.30'))
        self.assertEqual(VehicleCost.query.one().amount, Decimal('2144.30'))
        self.assertIsNone(VehicleCost.query.one().reimbursed_at)

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
