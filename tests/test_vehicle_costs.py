import csv
import io
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from werkzeug.exceptions import NotFound
import test_vehicle_service
from app.models import Expense, VehicleCost, ActivityLog
from app.services import vehicle_cost_service as costs, vehicle_service
from app.services.finance_summary_service import get_finance_summary
from app.services.financial_plan_service import build_financial_plan
from app.services.history_month_service import resolve_active_month
from app.utils import group_entries_by_month


class VehicleCostsTestCase(test_vehicle_service.VehicleServiceTestCase):
    def spend(self, vehicle, **kwargs):
        return costs.create_vehicle_spend(self.owner, vehicle, **(dict(amount='125.50', cost_date='2026-03-15',kind='fuel') | kwargs))

    def test_payer_and_financial_isolation(self):
        vehicle = self.vehicle(is_company=True, default_payer='organization')
        own = self.spend(vehicle,payer='self')
        before = get_finance_summary(self.owner.id,2026,3)
        plan = build_financial_plan(self.owner.id,today=date(2026,3,15))
        groups = group_entries_by_month(Expense.query.all(),'expense_date')
        self.assertIsInstance(self.spend(vehicle),VehicleCost)
        self.assertEqual(before,get_finance_summary(self.owner.id,2026,3))
        self.assertEqual(plan,build_financial_plan(self.owner.id,today=date(2026,3,15)))
        after = group_entries_by_month(Expense.query.all(),'expense_date')
        self.assertEqual(groups,after)
        self.assertEqual(resolve_active_month(groups),resolve_active_month(after))
        self.assertEqual(Expense.query.count(),1)
        self.assertEqual(own.category,'transport')
        self.assertEqual(own.vehicle_cost_kind,'fuel')

    def test_owner_archive_and_flag(self):
        vehicle = self.vehicle(is_company=True)
        cost = self.spend(vehicle,payer='organization',paid_by_driver=True)
        with self.assertRaises(NotFound):
            costs.create_vehicle_spend(self.other,vehicle,amount='1',cost_date='2026-03-15')
        with self.assertRaises(NotFound):
            costs.mark_reimbursed(self.other,cost.id,'2026-03-16')
        with self.assertRaises(NotFound):
            costs.vehicle_report(self.other,vehicle_ids=[vehicle.id])
        vehicle_service.archive_vehicle(self.owner,vehicle.id,'sold')
        with self.assertRaises(ValueError): self.spend(vehicle)
        with self.assertRaises(ValueError): costs.mark_reimbursed(self.owner,cost.id)
        self.login()
        self.app.config['VEHICLES_ENABLED']=False
        for url in ['/vehicles/reports',f'/vehicles/{vehicle.id}/spend']:
            self.assertEqual(self.client.get(url).status_code,404)

    def test_report_reimbursement_csv_and_boundaries(self):
        vehicle = self.vehicle(is_company=True)
        self.spend(vehicle,payer='self',cost_date='2026-02-28')
        cost=self.spend(vehicle,payer='organization',paid_by_driver=True,comment='Учебный; "чек"\nстрока')
        with patch('app.services.vehicle_service.utc_now',return_value=datetime(2026,2,28,22,tzinfo=timezone.utc)):
            report=costs.vehicle_report(self.owner)
        self.assertEqual(report['start'],date(2026,3,1))
        self.assertEqual(report['totals']['unpaid'],Decimal('125.50'))
        self.assertEqual(report['totals']['self'],0)
        data=costs.report_csv(self.owner,report)
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        parsed=list(csv.reader(io.StringIO(data.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(parsed[1][-1],cost.comment)
        costs.mark_reimbursed(self.owner,cost.id,'2026-03-16')
        self.assertEqual(costs.vehicle_report(self.owner,'2026-03-01','2026-03-31')['totals']['unpaid'],0)
        costs.mark_reimbursed(self.owner,cost.id)
        self.assertIsNone(cost.reimbursed_at)
        self.login()
        self.assertEqual(self.client.get('/vehicles/reports?date_from=2026-03-01&date_to=2026-03-31&day=2026-03-15').status_code,200)
        self.assertEqual(self.client.get('/vehicles/reports?date_from=2026-03-01&date_to=2026-03-31&format=csv').status_code,200)
        self.login(self.other)
        self.assertEqual(self.client.post(f'/vehicles/costs/{cost.id}/reimburse').status_code,404)
        for log in ActivityLog.query.all():
            self.assertNotIn('Учебный', log.description or '')

    def test_invalid_costs(self):
        vehicle=self.vehicle()
        with self.assertRaises(ValueError): self.spend(vehicle,payer='organization')
        for value in ['0','-1','0.001','10000000000']:
            with self.assertRaises(ValueError): self.spend(vehicle,amount=value)
        with self.assertRaises(ValueError): self.spend(vehicle,kind='invalid')
        with self.assertRaises(ValueError): costs.vehicle_report(self.owner,'2026-03-02','2026-03-01')

    def test_admin_privacy_and_form_routing(self):
        vehicle=self.vehicle(is_company=True)
        self.login()
        response=self.client.post('/expenses',data=dict(vehicle_id=vehicle.id,payer='organization',amount='15.50',expense_date='2026-03-15',title='Учебная трата',kind='fuel',paid_by_driver='on'))
        self.assertEqual(response.status_code,302)
        self.assertEqual(Expense.query.count(),0)
        self.assertEqual(VehicleCost.query.count(),1)
        self.login(self.admin)
        self.assertEqual(self.client.get(f'/vehicles/{vehicle.id}').status_code,404)
        self.assertEqual(self.client.get(f'/vehicles/reports?vehicle_id={vehicle.id}').status_code,404)
        self.assertEqual(self.client.post('/vehicles/costs/1/reimburse').status_code,404)

    def test_csv_formula_protection(self):
        vehicle=self.vehicle(is_company=True)
        self.spend(vehicle,payer='organization',comment='=1+1')
        data=costs.report_csv(self.owner,costs.vehicle_report(self.owner,'2026-03-01','2026-03-31'))
        self.assertIn("'=1+1",data.decode('utf-8-sig'))

    def test_csrf_and_account_deletion(self):
        from extensions import db
        vehicle=self.vehicle(is_company=True)
        self.spend(vehicle,payer='organization')
        self.login()
        self.app.config['WTF_CSRF_ENABLED']=True
        self.assertEqual(self.client.post(f'/vehicles/costs/1/reimburse',data=dict(date='2026-03-16')).status_code,400)
        self.app.config['WTF_CSRF_ENABLED']=False
        db.session.delete(self.owner)
        db.session.commit()
        self.assertEqual(VehicleCost.query.count(),0)

    def test_personal_kind_survives_custom_title(self):
        vehicle=self.vehicle()
        self.spend(vehicle,payer='self',kind='parts',title='Учебное название')
        report=costs.vehicle_report(self.owner,'2026-03-01','2026-03-31')
        self.assertEqual(report['kinds']['parts']['self'],Decimal('125.50'))
