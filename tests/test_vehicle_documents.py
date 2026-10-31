from datetime import timedelta
from unittest.mock import patch
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import NotFound
import test_vehicle_service
from app.models import VehicleDocument, Expense, VehicleCost, ActivityLog
from app.services import vehicle_document_service as documents, vehicle_maintenance_service as maintenance, vehicle_service as vehicles
from extensions import db


class VehicleDocumentTests(test_vehicle_service.VehicleServiceTestCase):
    def save(self, vehicle, **values):
        return documents.save_document(self.owner, vehicle.id, dict(kind='osago', issued_date='2026-01-15', expires_at='2027-01-15', amount='15000') | values)

    def test_validation_and_unique(self):
        vehicle = self.vehicle()
        for values in [dict(kind='invalid'), dict(amount='-100'), dict(amount='0'), dict(expires_at='2025-01-01'), dict(title='x'*151), dict(comment='x'*2001), dict(issued_date='bad')]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.save(vehicle, **values)
        row = self.save(vehicle)
        with self.assertRaises(ValueError): self.save(vehicle)
        db.session.add(VehicleDocument(vehicle_id=vehicle.id, kind='osago'))
        with self.assertRaises(IntegrityError): db.session.commit()
        db.session.rollback()
        log = ActivityLog.query.filter_by(action='vehicle_document_save').one()
        self.assertFalse(log.description)
        self.assertEqual(log.entity_type, 'vehicle_document')
        self.assertEqual(log.entity_id, row.id)

    def test_boundaries_and_edit(self):
        vehicle = self.vehicle()
        day = vehicles.today()
        row = self.save(vehicle, kind='pts', issued_date='', expires_at='', amount='')
        self.assertEqual(maintenance.document_condition(vehicle, day=day), 'ok')
        for days, expected in [(30,'ok'),(15,'ok'),(14,'soon'),(10,'soon'),(0,'overdue'),(-1,'overdue')]:
            documents.save_document(self.owner, vehicle.id, dict(kind='pts', expires_at=(day+timedelta(days=days)).isoformat()), row.id)
            with patch.object(vehicles, 'today', side_effect=AssertionError('unexpected clock call')):
                self.assertEqual(maintenance.technical_condition(vehicle, day=day, mileage=None), expected)
        self.assertEqual(documents.documents_for(vehicle, day=day)[0][1]['remaining_days'], -1)
        documents.save_document(self.owner, vehicle.id, dict(kind='pts'), row.id)
        self.assertEqual(maintenance.document_condition(vehicle, day=day), 'ok')

    def test_spending_and_rollback(self):
        for payer in ['self','organization']:
            vehicle = vehicles.create_vehicle(self.owner, dict(name='Страхование '+payer, is_company=True))
            row = self.save(vehicle, create_expense='on', payer=payer)
            self.assertIsNotNone(row.expense_id if payer=='self' else row.vehicle_cost_id)
            documents.save_document(self.owner, vehicle.id, dict(kind='osago', amount='15000'), row.id)
        self.assertEqual(Expense.query.count(), 1)
        self.assertEqual(VehicleCost.query.count(), 1)
        self.assertEqual(Expense.query.one().category, 'transport')
        self.assertEqual(Expense.query.one().vehicle_cost_kind, 'insurance')
        vehicle = self.vehicle()
        with patch('app.services.vehicle_cost_service.create_vehicle_spend', side_effect=ValueError('failure')):
            with self.assertRaises(ValueError): self.save(vehicle, create_expense='on')
        self.assertEqual(VehicleDocument.query.filter_by(vehicle_id=vehicle.id).count(), 0)
        row = self.save(vehicle)
        self.assertIsNone(row.expense_id)

    def test_ownership_archive_flag_and_trailer(self):
        vehicle = self.vehicle(kind='trailer')
        self.login()
        self.assertIn('Документы'.encode(), self.client.get(f'/vehicles/{vehicle.id}?tab=documents').data)
        self.save(vehicle, kind='pts', amount='', expires_at='')
        self.login(self.other)
        self.assertEqual(self.client.post(f'/vehicles/{vehicle.id}/documents').status_code, 404)
        self.login()
        vehicles.archive_vehicle(self.owner, vehicle.id, 'sold')
        with self.assertRaises(ValueError): self.save(vehicle)
        self.app.config['VEHICLES_ENABLED'] = False
        self.assertEqual(self.client.post(f'/vehicles/{vehicle.id}/documents').status_code,404)
        self.assertEqual(self.client.post(f'/vehicles/{vehicle.id}/documents/1').status_code,404)

    def test_expiring_scope_and_attention(self):
        vehicle = self.vehicle()
        self.save(vehicle, expires_at=(vehicles.today()+timedelta(days=10)).isoformat())
        self.assertEqual(len(documents.expiring_soon(self.owner)),1)
        self.assertEqual(documents.expiring_soon(self.other),[])
        self.login()
        self.assertIn(vehicle.name.encode(), self.client.get('/vehicles?attention=1').data)

    def test_expiring_query_boundaries_and_archive(self):
        vehicle = self.vehicle()
        day = vehicles.today()
        rows = [self.save(vehicle, kind=kind, issued_date='', expires_at=(day+timedelta(days=days)).isoformat())
                for kind, days in [('osago', 0), ('kasko', 14), ('sts', 15), ('license', -1)]]
        self.assertEqual([row.id for row in documents.expiring_soon(self.owner)], [rows[0].id, rows[1].id])
        vehicles.archive_vehicle(self.owner, vehicle.id, 'sold')
        self.assertEqual(documents.expiring_soon(self.owner), [])

    def test_edit_document_from_another_vehicle_is_not_found(self):
        first = self.vehicle()
        second = vehicles.create_vehicle(self.owner, dict(name='Второй автомобиль'))
        row = self.save(first)
        with self.assertRaises(NotFound):
            documents.save_document(self.owner, second.id, dict(kind='pts'), row.id)
        self.assertEqual(VehicleDocument.query.one().vehicle_id, first.id)
        self.assertEqual(VehicleDocument.query.one().kind, 'osago')

    def test_formatted_money_and_missing_spend_amount_rollback(self):
        from decimal import Decimal
        vehicle = self.vehicle()
        row = self.save(vehicle, amount='15 000,50')
        self.assertEqual(row.amount, Decimal('15000.50'))
        with self.assertRaises(ValueError):
            self.save(vehicle, kind='kasko', amount='', create_expense='on')
        self.assertEqual(VehicleDocument.query.count(), 1)
        self.assertEqual(Expense.query.count(), 0)

    def test_create_error_preserves_form_without_saving(self):
        vehicle = self.vehicle(is_company=True)
        self.login()
        response = self.client.post(f'/vehicles/{vehicle.id}/documents', data=dict(
            kind='kasko', title='Попытка <b>правки</b>', issued_date='2026-05-02',
            expires_at='2026-05-01', amount='2 500,75', comment='Не потерять',
            create_expense='on', payer='organization'))
        self.assertEqual(response.status_code, 422)
        html = response.get_data(as_text=True)
        for value in ['value="Попытка &lt;b&gt;правки&lt;/b&gt;"', 'value="2026-05-02"',
                      'value="2026-05-01"', 'value="2 500,75"', 'Не потерять',
                      '<option value="kasko" selected>', '<option value="organization" selected>',
                      'name="create_expense" checked', '<details class="mb-3" open>']:
            self.assertIn(value, html)
        self.assertEqual(VehicleDocument.query.count(), 0)
        self.assertEqual(VehicleCost.query.count(), 0)

    def test_edit_error_preserves_attempt_and_saved_record(self):
        vehicle = self.vehicle()
        row = self.save(vehicle, title='Исходное название')
        self.login()
        response = self.client.post(f'/vehicles/{vehicle.id}/documents/{row.id}', data=dict(
            kind='osago', title='Новое название', issued_date='2026-05-02', expires_at='2026-05-01', amount='7500'))
        self.assertEqual(response.status_code, 422)
        html = response.get_data(as_text=True)
        self.assertIn('value="Новое название"', html)
        self.assertIn('<details class="mt-3" open>', html)
        self.assertEqual(row.title, 'Исходное название')
        self.assertEqual(str(row.amount), '15000.00')

    def test_linked_amount_requires_choice_and_updates_both_payers(self):
        from decimal import Decimal
        for payer in ['self', 'organization']:
            vehicle = vehicles.create_vehicle(self.owner, dict(name='Согласование '+payer, is_company=True))
            row = self.save(vehicle, create_expense='on', payer=payer)
            model, spend_id = (Expense, row.expense_id) if payer == 'self' else (VehicleCost, row.vehicle_cost_id)
            spend = db.session.get(model, spend_id)
            title, original_date = spend.title, spend.expense_date if payer == 'self' else spend.cost_date
            data = dict(kind='osago', amount='2000')
            with self.assertRaises(ValueError): documents.save_document(self.owner, vehicle.id, data, row.id)
            self.assertEqual(row.amount, Decimal('15000'))
            self.assertEqual(spend.amount, Decimal('15000'))
            documents.save_document(self.owner, vehicle.id, data | dict(linked_amount_action='keep'), row.id)
            self.assertEqual(row.amount, Decimal('2000'))
            self.assertEqual(spend.amount, Decimal('15000'))
            documents.save_document(self.owner, vehicle.id, data | dict(amount='2200,50', linked_amount_action='update'), row.id)
            self.assertEqual(row.amount, Decimal('2200.50'))
            self.assertEqual(spend.amount, Decimal('2200.50'))
            self.assertEqual(spend.title, title)
            self.assertEqual(spend.expense_date if payer == 'self' else spend.cost_date, original_date)
            self.assertEqual(model.query.filter_by(vehicle_id=vehicle.id).count(), 1)
            self.assertFalse(ActivityLog.query.filter_by(action='vehicle_document_save').order_by(ActivityLog.id.desc()).first().description)

    def test_linked_amount_update_rolls_back_with_document(self):
        from decimal import Decimal
        for payer in ['self', 'organization']:
            vehicle = vehicles.create_vehicle(self.owner, dict(name='Откат '+payer, is_company=True))
            row = self.save(vehicle, create_expense='on', payer=payer)
            model, spend_id = (Expense, row.expense_id) if payer == 'self' else (VehicleCost, row.vehicle_cost_id)
            logs = ActivityLog.query.count()
            with patch('app.services.vehicle_document_service.record_activity', side_effect=ValueError('failure')):
                with self.assertRaises(ValueError):
                    documents.save_document(self.owner, vehicle.id, dict(kind='osago', amount='2000', linked_amount_action='update'), row.id)
            self.assertEqual(row.amount, Decimal('15000'))
            self.assertEqual(db.session.get(model, spend_id).amount, Decimal('15000'))
            self.assertEqual(ActivityLog.query.count(), logs)

    def test_deleted_expense_clears_document_link(self):
        from app.services import expense_service
        vehicle = self.vehicle()
        row = self.save(vehicle, create_expense='on')
        expense_service.delete_expense(user_id=self.owner.id, expense_id=row.expense_id)
        self.assertIsNone(row.expense_id)
        documents.save_document(self.owner, vehicle.id, dict(kind='osago', amount='2000'), row.id)
        self.assertEqual(Expense.query.count(), 0)

    def test_linked_amount_cannot_update_another_owners_expense(self):
        from datetime import date
        from decimal import Decimal
        vehicle = self.vehicle()
        row = self.save(vehicle)
        foreign = Expense(user_id=self.other.id, vehicle_id=vehicle.id,
            title='Чужая трата', category='transport', amount=1000, expense_date=date(2026, 1, 15))
        db.session.add(foreign)
        db.session.flush()
        row.expense_id = foreign.id
        db.session.commit()
        with self.assertRaises(NotFound):
            documents.save_document(self.owner, vehicle.id, dict(kind='osago', amount='2000', linked_amount_action='update'), row.id)
        self.assertEqual(foreign.amount, Decimal('1000'))
        self.assertEqual(row.amount, Decimal('15000'))

    def test_monthly_linked_expense_requires_separate_edit(self):
        from decimal import Decimal
        vehicle = self.vehicle()
        row = self.save(vehicle, create_expense='on')
        spend = db.session.get(Expense, row.expense_id)
        spend.is_monthly = True
        db.session.commit()
        with self.assertRaisesRegex(ValueError, 'Ежемесячную'):
            documents.save_document(self.owner, vehicle.id, dict(kind='osago', amount='2000', linked_amount_action='update'), row.id)
        self.assertEqual(spend.amount, Decimal('15000'))
        documents.save_document(self.owner, vehicle.id, dict(kind='osago', amount='2000', linked_amount_action='keep'), row.id)
        self.assertEqual(row.amount, Decimal('2000'))
        self.assertEqual(spend.amount, Decimal('15000'))
        self.assertTrue(spend.is_monthly)
