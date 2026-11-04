from datetime import timedelta, datetime, timezone
from unittest.mock import patch
import test_vehicle_service
from app.models import VehicleDocument, VehicleMaintenanceItem, VehicleNotificationLog, ActivityLog
from app.services import vehicle_notification_service as service, vehicle_service as vehicles
from extensions import db


class NotificationTests(test_vehicle_service.VehicleServiceTestCase):
    def test_admin_switch_controls_delivery_and_persists(self):
        from app.routes.admin import SETTINGS_FIELDS, _setting_default
        from app.utils import get_setting
        self.vehicle()
        self.login(self.admin)
        self.assertIn(b'vehicle_notifications_enabled', self.client.get('/admin/settings').data)
        form = {key: _setting_default(key) for key in SETTINGS_FIELDS}
        form['vehicle_notifications_enabled'] = 'false'
        self.assertEqual(self.client.post('/admin/settings', data=form).status_code, 302)
        self.assertEqual(get_setting('vehicle_notifications_enabled'), 'false')
        with patch.object(service.telegram, 'send_telegram_message') as sender:
            self.assertEqual(service.send_due_notifications(), dict(sent=0, skipped=0, failed=0))
            sender.assert_not_called()
        self.assertEqual(self.client.post('/internal/vehicle-notifications/run').status_code, 404)
        self.app.config['VEHICLE_NOTIFICATIONS_ENABLED'] = False
        form['vehicle_notifications_enabled'] = 'true'
        self.assertEqual(self.client.post('/admin/settings', data=form).status_code, 302)
        self.assertEqual(get_setting('vehicle_notifications_enabled'), 'true')
        with patch.object(service.telegram, 'send_telegram_message', return_value=True) as sender:
            self.assertEqual(service.send_due_notifications()['sent'], 1)
            sender.assert_called_once()

    def test_regular_users_cannot_change_global_switch(self):
        from app.utils import get_setting
        for user in (self.owner, self.other):
            self.login(user)
            response = self.client.post('/admin/settings', data={
                'vehicle_notifications_enabled': 'true'})
            self.assertIn(response.status_code, (302, 403))
            self.assertIsNone(get_setting('vehicle_notifications_enabled'))

    def setUp(self):
        super().setUp()
        self.app.config.update(VEHICLE_NOTIFICATIONS_ENABLED=True, OFFICIUM_PUBLIC_URL='https://example.test')
        self.owner.notification_delivery_time = '00:00'
        db.session.commit()

    def test_collect_boundaries_privacy_archive(self):
        vehicle = self.vehicle(vin='SECRET12345678901', plate='А123ВС77', registration_country_id=self.ru.id)
        day = vehicles.today()
        db.session.add(VehicleMaintenanceItem(vehicle_id=vehicle.id, name='Масло', interval_months=1, last_done_date=day-timedelta(days=90), is_active=True))
        for kind, days in [('osago',13),('kasko',14),('sts',15),('pts',None)]:
            db.session.add(VehicleDocument(vehicle_id=vehicle.id, kind=kind, expires_at=day+timedelta(days=days) if days else None, amount=12345))
        db.session.commit()
        notices = service.due_notifications(self.owner)
        self.assertEqual([n['kind'] for n in notices], ['mileage_due','maintenance_due','document_expiring','document_expiring'])
        self.assertEqual(len({n['payload_hash'] for n in notices}),4)
        texts = str(notices)
        for private in [vehicle.vin,vehicle.plate,'12345']: self.assertNotIn(private,texts)
        self.assertIn(vehicle.name,texts)
        for notice in notices:
            self.assertIn(notice['url'], notice['text'])
        self.assertTrue(
            notices[1]['url'].endswith(f'/vehicles/{vehicle.id}?tab=maintenance')
        )
        self.assertTrue(
            notices[2]['url'].endswith(f'/vehicles/{vehicle.id}?tab=documents')
        )
        vehicles.archive_vehicle(self.owner,vehicle.id,'sold')
        self.assertEqual(service.due_notifications(self.owner),[])
        self.assertEqual(service.due_notifications(self.other),[])

    def test_delivery_dedup_failure_next_day_and_audit(self):
        self.vehicle()
        now = datetime(2026,11,15,22,tzinfo=timezone.utc)
        with patch.object(service.telegram,'send_telegram_message',return_value=True) as sender:
            self.assertEqual(service.send_due_notifications(now),dict(sent=1,skipped=0,failed=0))
            self.assertEqual(service.send_due_notifications(now),dict(sent=0,skipped=1,failed=0))
            self.assertEqual(sender.call_count,1)
            button = sender.call_args.kwargs['reply_markup']['inline_keyboard'][0][0]
            self.assertEqual(button['url'], 'https://example.test/vehicles')
        with patch.object(service.telegram,'send_telegram_message',side_effect=RuntimeError('private token')):
            self.assertEqual(service.send_due_notifications(now+timedelta(days=1))['failed'],1)
            self.assertEqual(service.send_due_notifications(now+timedelta(days=1))['skipped'],1)
        self.assertEqual([r.delivered for r in VehicleNotificationLog.query.order_by(VehicleNotificationLog.id)], [True,False])
        logs = ActivityLog.query.filter_by(action='vehicle_notification_send').all()
        self.assertEqual(len(logs),2)
        self.assertTrue(all(l.entity_type=='vehicle_notification' and l.description in ('delivered=true','delivered=false') for l in logs))
        first = service.due_notifications(self.owner,day=now.astimezone(vehicles.app_timezone()).date())[0]
        self.assertEqual(first['payload_hash'], VehicleNotificationLog.query.first().payload_hash)

    def test_flags_and_profile(self):
        self.vehicle()
        self.login()
        self.assertIn('Напоминания об автомобилях'.encode(), self.client.get('/profile').data)
        self.assertEqual(self.client.post('/profile',data={}).status_code,302)
        self.assertFalse(self.owner.vehicle_notifications_enabled)
        with patch.object(service.telegram,'send_telegram_message') as sender:
            self.assertEqual(service.send_due_notifications()['sent'],0)
            sender.assert_not_called()
        self.app.config['VEHICLE_NOTIFICATIONS_ENABLED']=False
        self.assertEqual(self.client.post('/internal/vehicle-notifications/run').status_code,404)

    def test_audit_batch_and_snooze(self):
        from sqlalchemy import event
        for n in range(6): vehicles.create_vehicle(self.owner, dict(name=f'Авто {n}'))
        db.session.expire_all()
        statements=[]
        def capture(conn,cursor,statement,parameters,context,executemany):
            if statement.lstrip().upper().startswith('SELECT'): statements.append(statement)
        event.listen(db.engine,'before_cursor_execute',capture)
        try: self.assertEqual(len(vehicles.due_vehicles(self.owner)),6)
        finally: event.remove(db.engine,'before_cursor_execute',capture)
        self.assertLessEqual(len(statements),3)
        vehicle=vehicles.due_vehicles(self.owner)[0]
        vehicles.snooze(self.owner,[vehicle.id],days=7)
        self.assertEqual(vehicle.mileage_snooze_until,vehicles.today()+timedelta(days=7))
