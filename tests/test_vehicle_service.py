import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import NotFound
from flask import g

from app import create_app
from app.models import ActivityLog, Expense, RegistrationCountry, User, Vehicle, VehicleOdometerReading
from app.services import expense_service as expenses, vehicle_service as service
from extensions import db


class VehicleServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(dict(TESTING=True, SECRET_KEY='vehicle-tests', ENVIRONMENT='testing',
            VEHICLES_ENABLED=True, WTF_CSRF_ENABLED=False, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_ENGINE_OPTIONS={}, APP_TIMEZONE='Europe/Moscow', MONTHLY_EXPENSES_TODAY=date(2026, 3, 15)))
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.owner = User(telegram_id=70001, first_name='Вымышленный владелец')
        self.other = User(telegram_id=70002)
        self.admin = User(telegram_id=70003, role='superadmin')
        self.ru = RegistrationCountry(code='RU', name='Россия', strict_format=True)
        self.ab = RegistrationCountry(code='AB', name='Абхазия')
        db.session.add_all([self.owner, self.other, self.admin, self.ru, self.ab])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def vehicle(self, **values):
        return service.create_vehicle(self.owner, dict(name='Вымышленный автомобиль', **values))

    def login(self, user=None):
        g.pop('_login_user', None)
        with self.client.session_transaction() as session:
            session['_user_id'] = str((user or self.owner).id)
            session['_fresh'] = True

    def expense(self, vehicle, **values):
        return expenses.create_expense(user_id=self.owner.id, vehicle_id=vehicle.id,
            **(dict(amount='123.45', title='Вымышленная трата', expense_date='2026-01-15') | values))

    def test_plate_normalization(self):
        for incoming, expected in [(' a 123 bc 77 ', 'А123ВС77'), ('x001yx777', 'Х001УХ777'), ('А123ВС777', 'А123ВС777')]:
            self.assertEqual(service.normalize_plate(incoming, self.ru), expected)
        for invalid in ['123ABC', 'D123BC77', 'А123ВС7', 'А123ВС7777', 'А１２３ВС77']:
            with self.assertRaises(ValueError):
                service.normalize_plate(invalid, self.ru)
        self.assertEqual(service.normalize_plate(' ab 123 ', self.ab), 'AB 123')
        self.assertIsNone(service.normalize_plate(' ', self.ru))
        with self.assertRaises(ValueError):
            service.normalize_plate('A'*13, self.ab)

    def test_restore_trailer_requires_restored_tractor(self):
        tractor = self.vehicle(kind='tractor_unit')
        trailer = service.create_vehicle(self.owner, dict(name='Прицеп', kind='trailer', tractor_id=tractor.id))
        service.archive_vehicle(self.owner, trailer.id, 'sold')
        service.archive_vehicle(self.owner, tractor.id, 'sold')
        with self.assertRaisesRegex(ValueError, 'Сначала восстановите тягач'):
            service.restore_vehicle(self.owner, trailer.id)
        self.assertIsNotNone(trailer.archived_at)
        service.restore_vehicle(self.owner, tractor.id)
        service.restore_vehicle(self.owner, trailer.id)
        self.assertIsNone(trailer.archived_at)
        self.assertEqual(trailer.tractor_id, tractor.id)

    def test_disabled_module_keeps_existing_expense_editable(self):
        vehicle = self.vehicle()
        expense = self.expense(vehicle)
        self.login()
        self.app.config['VEHICLES_ENABLED'] = False
        response = self.client.post(f'/expenses/edit/{expense.id}', data=dict(
            amount='124.50', category='transport', title='Правка', expense_date='2026-01-15'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(expense.amount, Decimal('124.50'))
        self.assertEqual(expense.vehicle_id, vehicle.id)
        with self.assertRaises(NotFound):
            self.expense(vehicle)
        expenses.delete_expense(user_id=self.owner.id, expense_id=expense.id)
        self.assertIsNone(db.session.get(Expense, expense.id))

    def test_disabled_module_preserves_archive_and_ownership_guards(self):
        vehicle = self.vehicle()
        expense = self.expense(vehicle)
        service.archive_vehicle(self.owner, vehicle.id, 'sold')
        self.app.config['VEHICLES_ENABLED'] = False
        with self.assertRaises(ValueError):
            expenses.update_expense(user_id=self.owner.id, expense_id=expense.id,
                amount='200', category='transport', title='Правка', expense_date='2026-01-15')
        with self.assertRaises(ValueError):
            expenses.delete_expense(user_id=self.owner.id, expense_id=expense.id)
        with self.assertRaises(NotFound):
            expenses.update_expense(user_id=self.other.id, expense_id=expense.id,
                amount='200', category='transport', title='Чужой', expense_date='2026-01-15')

    def test_create_update_and_private_audit(self):
        vehicle = self.vehicle(vin='abcdefghjklmn1234', plate='a123bc77', registration_country_id=self.ru.id)
        self.assertEqual(vehicle.vin, 'ABCDEFGHJKLMN1234')
        service.update_vehicle(self.owner, vehicle.id, dict(color='#ffaa00', is_company='on', default_payer='organization'))
        self.assertEqual(vehicle.color, '#ffaa00')
        self.assertTrue(vehicle.is_company)
        self.assertEqual(vehicle.default_payer, 'organization')
        for log in ActivityLog.query.all():
            self.assertIsNone(log.description)
            self.assertEqual(log.entity_type, 'vehicle')

    def test_validation_is_atomic(self):
        vehicle = self.vehicle()
        for values in [dict(name=''), dict(name='x'*151), dict(make='x'*81), dict(model='x'*81), dict(year='x'*21),
            dict(vin='I'*17), dict(kind='invalid'), dict(body_type='invalid'), dict(fuel_type='invalid'),
            dict(color='red'), dict(status='invalid'), dict(default_payer='organization'), dict(default_payer='invalid'),
            dict(mileage_reminder_days=-1), dict(mileage_reminder_days='1.5'), dict(plate='ab123'),
            dict(registration_country_id=99999)]:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    service.update_vehicle(self.owner, vehicle.id, values)
                db.session.rollback()
                self.assertEqual(vehicle.name, 'Вымышленный автомобиль')
        with self.assertRaises(ValueError):
            self.vehicle()

    def test_disabled_country_and_unique_names_include_archive(self):
        vehicle = self.vehicle(plate='a123bc77', registration_country_id=self.ru.id)
        self.ru.is_active = False
        db.session.commit()
        service.update_vehicle(self.owner, vehicle.id, dict(make='Пример'))
        with self.assertRaises(ValueError):
            service.create_vehicle(self.owner, dict(name='Другая', registration_country_id=self.ru.id))
        service.archive_vehicle(self.owner, vehicle.id, 'sold')
        with self.assertRaises(ValueError):
            self.vehicle()

    def test_ownership_for_service_operations(self):
        vehicle = self.vehicle()
        for operation in [lambda: service.get_vehicle_for_user(self.other, vehicle.id),
            lambda: service.update_vehicle(self.other, vehicle.id, {}),
            lambda: service.archive_vehicle(self.other, vehicle.id, 'sold'),
            lambda: service.restore_vehicle(self.other, vehicle.id),
            lambda: service.add_odometer_reading(self.other, vehicle.id, '2026-01-01', 100),
            lambda: service.snooze(self.other, [vehicle.id]),
            lambda: service.vehicle_expenses(self.other, vehicle.id)]:
            with self.assertRaises(NotFound):
                operation()
        with self.assertRaises(NotFound):
            service.get_vehicle_for_user(self.owner, 999999)

    def test_archive_readonly_expenses_and_restore(self):
        vehicle = self.vehicle()
        expense = self.expense(vehicle)
        self.assertEqual(expense.category, 'transport')
        self.assertEqual(expense.amount, Decimal('123.45'))
        service.archive_vehicle(self.owner, vehicle.id, 'other', 'Приватное примечание')
        for operation in [lambda: service.update_vehicle(self.owner, vehicle.id, {}),
            lambda: service.add_odometer_reading(self.owner, vehicle.id, '2026-01-01', 100),
            lambda: self.expense(vehicle), lambda: expenses.delete_expense(user_id=self.owner.id, expense_id=expense.id),
            lambda: expenses.update_expense(user_id=self.owner.id, expense_id=expense.id, amount=200, category='transport', title='Пример', expense_date='2026-01-15'),
            lambda: expenses.update_expense(user_id=self.owner.id, expense_id=expense.id, vehicle_id=None, amount=expense.amount, category='transport', title='Пример', expense_date='2026-01-15')]:
            with self.assertRaises(ValueError):
                operation()
        service.restore_vehicle(self.owner, vehicle.id)
        self.assertIsNone(vehicle.archived_at)
        expenses.update_expense(user_id=self.owner.id, expense_id=expense.id, amount=200, category='transport', title='Пример', expense_date='2026-01-15')
        self.assertEqual(expense.amount, Decimal('200'))

    def test_expense_owner_validation_on_create_and_update(self):
        vehicle = self.vehicle()
        other_vehicle = service.create_vehicle(self.other, dict(name='Чужой автомобиль'))
        with self.assertRaises(NotFound):
            self.expense(other_vehicle)
        expense = self.expense(vehicle)
        with self.assertRaises(NotFound):
            expenses.update_expense(user_id=self.owner.id, expense_id=expense.id, amount='123.45', category='transport', title='Пример', expense_date='2026-01-15', vehicle_id=other_vehicle.id)

    def test_odometer_monotonic_both_directions_and_invalid_values(self):
        vehicle = self.vehicle()
        service.add_odometer_reading(self.owner, vehicle.id, '2026-01-10', 100)
        service.add_odometer_reading(self.owner, vehicle.id, '2026-01-20', 200)
        for day, km, source in [('2026-01-15', 99, 'manual'), ('2026-01-15', 201, 'manual'),
            ('2026-01-10', 100, 'manual'), ('2099-01-01', 500, 'manual'), ('bad', 500, 'manual'),
            ('2026-01-15', '1.5', 'manual'), ('2026-01-15', -1, 'manual'), ('2026-01-15', 150, 'bad')]:
            with self.assertRaises(ValueError):
                service.add_odometer_reading(self.owner, vehicle.id, day, km, source)
        service.add_odometer_reading(self.owner, vehicle.id, '2026-01-15', 150)
        self.assertEqual(service.current_mileage(vehicle), 200)
        self.assertEqual(len(vehicle.readings), 3)

    def test_grouped_mileage_keeps_integer_validation(self):
        vehicle = self.vehicle()
        for value in ('123 654', '123\u00a0654', '123\u202f654'):
            self.assertEqual(service._integer(value, 'Пробег'), 123654)
        for value in ('-123 654', '123,654', '123.654', '2 147 483 648'):
            with self.assertRaises(ValueError):
                service._integer(value, 'Пробег')
        service.add_odometer_reading(self.owner, vehicle.id, '2026-01-10', '123 654')
        self.assertEqual(service.current_mileage(vehicle), 123654)

    def test_reminder_boundaries_and_snooze(self):
        vehicle = self.vehicle()
        day = service.today()
        self.assertTrue(service.needs_mileage_update(vehicle, day))
        self.assertIsNone(service.current_mileage(vehicle))
        service.add_odometer_reading(self.owner, vehicle.id, (day-timedelta(days=30)).isoformat(), 100)
        self.assertFalse(service.needs_mileage_update(vehicle, day))
        self.assertTrue(service.needs_mileage_update(vehicle, day+timedelta(days=1)))
        with patch.object(service, 'today', return_value=day+timedelta(days=1)):
            self.assertEqual(len(service.due_vehicles(self.owner)), 1)
            service.snooze(self.owner, [vehicle.id])
            self.assertFalse(service.needs_mileage_update(vehicle, day+timedelta(days=3)))
            self.assertTrue(service.needs_mileage_update(vehicle, day+timedelta(days=4)))
        vehicle.status = 'in_repair'
        self.assertFalse(service.needs_mileage_update(vehicle, day+timedelta(days=40)))
        vehicle.status, vehicle.mileage_reminder_days = 'active', 0
        self.assertFalse(service.needs_mileage_update(vehicle, day+timedelta(days=40)))

    def test_timezone_today(self):
        with patch.object(service, 'utc_now', return_value=datetime(2026, 10, 26, 22, 30, tzinfo=timezone.utc)):
            self.assertEqual(service.today(), date(2026, 10, 27))

    def test_tractor_trailer_atomic_creation_and_restrictions(self):
        tractor = service.create_with_trailer(self.owner, dict(name='Тестовый тягач', kind='tractor_unit', trailer_name='Тестовый прицеп'))
        trailer = tractor.trailers[0]
        self.assertEqual((trailer.kind, trailer.body_type, trailer.fuel_type), ('trailer', 'trailer', 'none'))
        self.assertFalse(service.needs_mileage_update(trailer, service.today()))
        with self.assertRaises(ValueError):
            service.add_odometer_reading(self.owner, trailer.id, '2026-01-01', 100)
        with self.assertRaises(ValueError):
            service.archive_vehicle(self.owner, tractor.id, 'sold')
        with self.assertRaises(ValueError):
            service.update_vehicle(self.owner, tractor.id, dict(kind='passenger'))
        with self.assertRaises(ValueError):
            service.update_vehicle(self.owner, trailer.id, dict(tractor_id=trailer.id))
        foreign = service.create_vehicle(self.other, dict(name='Чужой тягач', kind='tractor_unit'))
        with self.assertRaises(NotFound):
            service.update_vehicle(self.owner, trailer.id, dict(tractor_id=foreign.id))
        service.archive_vehicle(self.owner, trailer.id, 'sold')
        service.archive_vehicle(self.owner, tractor.id, 'sold')
        self.assertIsNotNone(tractor.archived_at)

    def test_monthly_copies_keep_vehicle_and_archive_suppresses_generation(self):
        from app.services.monthly_expenses_service import generate_monthly_expenses_from_start_date
        vehicle = self.vehicle()
        expense = self.expense(vehicle, is_monthly=True)
        copies = Expense.query.filter_by(monthly_group_id=expense.monthly_group_id).all()
        self.assertEqual(len(copies), 3)
        self.assertTrue(all(row.vehicle_id == vehicle.id for row in copies))
        service.archive_vehicle(self.owner, vehicle.id, 'sold')
        generate_monthly_expenses_from_start_date(expense.id, end_month=date(2026, 5, 1))
        self.assertEqual(Expense.query.count(), 3)

    def test_mileage_batch_atomic_and_snooze(self):
        vehicle = self.vehicle()
        other = service.create_vehicle(self.owner, dict(name='Вторая'))
        data = MultiDict([('vehicle_ids', str(vehicle.id)), ('vehicle_ids', str(other.id)), (f'km_{vehicle.id}', '100'), (f'km_{other.id}', '-1')])
        with self.assertRaises(ValueError):
            service.update_mileage_batch(self.owner, data)
        db.session.rollback()
        self.assertEqual(VehicleOdometerReading.query.count(), 0)
        data[f'km_{other.id}'] = '200'
        service.update_mileage_batch(self.owner, data)
        self.assertEqual(VehicleOdometerReading.query.count(), 2)
        data['action'] = 'snooze'
        service.update_mileage_batch(self.owner, data)
        self.assertEqual(vehicle.mileage_snooze_until, service.today()+timedelta(days=3))

    def test_all_vehicle_routes_idor(self):
        vehicle = self.vehicle()
        self.login(self.other)
        for method, path, data in [('get', '', {}), ('get', '/edit', {}), ('post', '/edit', {'name':'Hacked'}),
            ('post', '/archive', {'reason':'sold'}), ('post', '/restore', {}), ('get', '/expense', {}),
            ('post', '/odometer', {'reading_date':'2026-01-01','odometer_km':'100'})]:
            with self.subTest(path=path, method=method):
                response = getattr(self.client, method)(f'/vehicles/{vehicle.id}{path}', data=data)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.post('/vehicles/mileage', data={'vehicle_ids':vehicle.id, 'action':'snooze'}).status_code, 404)
        values=dict(vehicle_id=vehicle.id,amount='100',title='Тест',category='transport',expense_date='2026-01-01')
        self.assertEqual(self.client.post('/expenses', data=values).status_code, 404)
        self.login()
        own_expense = expenses.create_expense(user_id=self.other.id,amount=100,category='other',title='Тест',expense_date='2026-01-01')
        self.login(self.other)
        self.assertEqual(self.client.post(f'/expenses/edit/{own_expense.id}', data=values).status_code, 404)

    def test_disabled_flag_404_and_no_menu_including_anonymous(self):
        vehicle = self.vehicle()
        self.app.config['VEHICLES_ENABLED'] = False
        for logged_in in (False, True):
            if logged_in:
                self.login()
            for path in ['/vehicles', '/vehicles/new', f'/vehicles/{vehicle.id}', f'/vehicles/{vehicle.id}/edit',
                f'/vehicles/{vehicle.id}/expense', '/admin/registration-countries']:
                self.assertEqual(self.client.get(path).status_code, 404)
            for path in ['/vehicles/mileage',f'/vehicles/{vehicle.id}/archive',f'/vehicles/{vehicle.id}/restore',f'/vehicles/{vehicle.id}/odometer']:
                self.assertEqual(self.client.post(path).status_code, 404)
        self.assertNotIn('href="/vehicles"',self.client.get('/').get_data(as_text=True))
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(self.client.post('/vehicles/new').status_code, 404)

    def test_csrf_and_demo_are_preserved(self):
        self.login()
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(self.client.post('/vehicles/new',data={'name':'Недопустимый POST'}).status_code,400)
        self.assertEqual(Vehicle.query.count(),0)
        from app.routes.auth import LocalTestUser
        with self.assertRaises(NotFound):
            service.create_vehicle(LocalTestUser(),dict(name='Демо'))

    def test_country_validation_and_admin_role(self):
        for data in [dict(code='1',name='Тест'),dict(code='ZZ',name=''),dict(code='ZZ',name='x'*101),dict(code='RU',name='Дубликат')]:
            with self.assertRaises(ValueError):
                service.save_country(self.admin,data)
        with self.assertRaises(ValueError):
            service.save_country(self.admin,dict(code='ZZ',name='Попытка сменить код'),self.ru.id)
        from werkzeug.exceptions import Forbidden
        with self.assertRaises(Forbidden):
            service.save_country(self.owner,dict(code='ZZ',name='Тест'))

    def test_database_failure_does_not_log_private_vehicle_fields(self):
        from sqlalchemy.exc import IntegrityError
        self.login()
        private='ABCDEFGHJKLMN1234'
        with patch('app.services.vehicle_service.db.session.flush',side_effect=IntegrityError('INSERT',{'vin':private},Exception(private))):
            with self.assertLogs(self.app.logger,level='ERROR') as messages:
                response=self.client.post('/vehicles/new',data=dict(name='Тест',vin=private))
        self.assertEqual(response.status_code,200)
        self.assertNotIn(private,' '.join(messages.output))
        self.assertEqual(Vehicle.query.count(),0)

    def test_pagination_has_no_vehicle_limit(self):
        for index in range(25):
            service.create_vehicle(self.owner,dict(name=f'Тестовый {index}'),commit=False)
        db.session.commit()
        self.login()
        with self.app.test_request_context('/vehicles?page=2'):
            from flask import request
            page=service.list_vehicles(self.owner,request.args)
            self.assertEqual(page.total,25)
            self.assertEqual(page.pages,3)
            self.assertEqual(len(page.items),12)

    def test_import_cannot_change_archived_vehicle_amount(self):
        from app.routes.expenses import _update_monthly_expense_from_import
        vehicle=self.vehicle()
        expense=self.expense(vehicle,is_monthly=True)
        service.archive_vehicle(self.owner,vehicle.id,'sold')
        self.login()
        with self.app.test_request_context('/expenses/import'):
            with self.assertRaises(ValueError):
                _update_monthly_expense_from_import(expense,Decimal('200'),date(2026,1,15),'card',None)
        self.assertEqual(expense.amount,Decimal('123.45'))

    def test_render_screens_and_form_routes(self):
        vehicle = self.vehicle(registration_country_id=self.ru.id, plate='a123bc77')
        self.login()
        for path in ['/vehicles', '/vehicles/new', f'/vehicles/{vehicle.id}', f'/vehicles/{vehicle.id}?tab=mileage',
            f'/vehicles/{vehicle.id}?tab=expenses', f'/vehicles/{vehicle.id}/edit', f'/expenses?vehicle_id={vehicle.id}']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        for query in ['?q=Вымышленный', '?kind=truck', '?status=in_repair', '?archive=all', '?archive=archived', '?page=999']:
            self.assertEqual(self.client.get('/vehicles'+query).status_code, 200)
        response=self.client.post('/vehicles/new',data={'name':'Маршрутный тест'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.post(f'/vehicles/{vehicle.id}/odometer',data=dict(reading_date='2026-01-01',odometer_km='150')).status_code,302)
        self.assertEqual(self.client.post(f'/vehicles/{vehicle.id}/edit',data=dict(name='Новое название')).status_code,302)
        self.assertEqual(self.client.get(f'/vehicles/{vehicle.id}/expense').status_code,302)
        self.client.post(f'/vehicles/{vehicle.id}/archive',data=dict(reason='sold'))
        self.assertIn('Только просмотр',self.client.get(f'/vehicles/{vehicle.id}').get_data(as_text=True))
        self.assertEqual(self.client.get(f'/vehicles/{vehicle.id}/edit').status_code,302)
        self.assertEqual(self.client.get(f'/vehicles/{vehicle.id}/expense').status_code,302)
        self.client.post(f'/vehicles/{vehicle.id}/restore')
        self.assertIsNone(db.session.get(Vehicle,vehicle.id).archived_at)

    def test_expense_vehicle_field_is_limited_to_vehicle_flow(self):
        vehicle = self.vehicle()
        self.login()
        self.assertNotIn('id="expenseVehicle"', self.client.get('/expenses').get_data(as_text=True))
        response = self.client.get(f'/vehicles/{vehicle.id}/expense', follow_redirects=True)
        self.assertIn('id="expenseVehicle"', response.get_data(as_text=True))
        self.assertIn('name="vehicle_expense_context" value="1"', response.get_data(as_text=True))
        # Keep the vehicle flow after validation, even if the selection is cleared.
        response = self.client.post('/expenses', data=dict(vehicle_expense_context='1', vehicle_id='', amount='0',
            category='transport', title='Тест', expense_date='2026-01-01'))
        self.assertIn('id="expenseVehicle"', response.get_data(as_text=True))
        response = self.client.post('/expenses', data=dict(amount='0', category='transport', title='Тест', expense_date='2026-01-01'))
        self.assertNotIn('id="expenseVehicle"', response.get_data(as_text=True))
        linked = self.expense(vehicle)
        self.assertIn('id="expenseVehicle"', self.client.get(f'/expenses/edit/{linked.id}').get_data(as_text=True))
        ordinary = expenses.create_expense(user_id=self.owner.id, amount=100, category='other', title='Тест', expense_date='2026-01-01')
        self.assertNotIn('id="expenseVehicle"', self.client.get(f'/expenses/edit/{ordinary.id}').get_data(as_text=True))

    def test_country_administration_and_privacy(self):
        vehicle = self.vehicle(vin='ABCDEFGHJKLMN1234', plate='a123bc77', registration_country_id=self.ru.id)
        service.archive_vehicle(self.owner,vehicle.id,'sold','Секретное примечание')
        for user in [self.owner, self.other]:
            self.login(user)
            self.assertEqual(self.client.get('/admin/registration-countries').status_code, 302)
        self.login(self.admin)
        self.assertEqual(self.client.post('/admin/registration-countries',data=dict(code='ZZ', name='Тестовая страна',is_active='on')).status_code,200)
        country=RegistrationCountry.query.filter_by(code='ZZ').one()
        self.client.post(f'/admin/registration-countries/{country.id}',data=dict(name='Тестовая страна'))
        self.assertFalse(country.is_active)
        for path in ['/admin','/admin/users','/admin/logs','/admin/dictionaries','/admin/registration-countries',f'/admin/users/{self.owner.id}']:
            response=self.client.get(path)
            for secret in [vehicle.name, vehicle.vin, vehicle.plate,'Секретное примечание']:
                self.assertNotIn(secret,response.get_data(as_text=True))
        self.assertEqual(self.client.get(f'/vehicles/{vehicle.id}').status_code,404)

    def test_account_delete_cascades_vehicles_and_readings(self):
        vehicle=self.vehicle()
        service.add_odometer_reading(self.owner,vehicle.id,'2026-01-01',100)
        db.session.delete(self.owner)
        db.session.commit()
        self.assertEqual(Vehicle.query.count(),0)
        self.assertEqual(VehicleOdometerReading.query.count(),0)
