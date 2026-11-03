"""Offline checks exercise the real Telegram adapter down to its HTTP boundary."""
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch
import requests
import test_vehicle_service
from app.models import VehicleDocument, VehicleMaintenanceItem, VehicleNotificationLog
from app.services import vehicle_service as vehicles, vehicle_notification_service as notifications
from extensions import db


class NotificationTransportTests(unittest.TestCase):
    tearDown = test_vehicle_service.VehicleServiceTestCase.tearDown
    vehicle = test_vehicle_service.VehicleServiceTestCase.vehicle
    def setUp(self):
        test_vehicle_service.VehicleServiceTestCase.setUp(self)
        self.app.config.update(VEHICLE_NOTIFICATIONS_ENABLED=True,
            OFFICIUM_PUBLIC_URL='https://example.test', TELEGRAM_BOT_TOKEN='offline-test-token')
        self.owner.notification_delivery_time = '00:00'
        vehicle = self.vehicle()
        day = vehicles.today()
        db.session.add_all([
            VehicleDocument(vehicle_id=vehicle.id, kind='osago', expires_at=day+timedelta(days=13)),
            VehicleMaintenanceItem(vehicle_id=vehicle.id, name='Масло', interval_months=1,
                last_done_date=day-timedelta(days=90), is_active=True)])
        db.session.commit()

    def test_real_adapter_payload_and_dedup(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'ok': True}
        with patch('app.services.telegram_bot_service.requests.post', return_value=response) as post:
            self.assertEqual(self.client.post('/internal/vehicle-notifications/run').json,
                             dict(sent=3, skipped=0, failed=0))
            self.assertEqual(post.call_count, 3)
            for call in post.call_args_list:
                self.assertEqual(call.args[0], 'https://api.telegram.org/botoffline-test-token/sendMessage')
                self.assertEqual(call.kwargs['json']['chat_id'], self.owner.telegram_id)
                self.assertIn('https://example.test/vehicles', call.kwargs['json']['text'])
                self.assertEqual(call.kwargs['timeout'], 10)
            self.assertEqual(self.client.post('/internal/vehicle-notifications/run').json,
                             dict(sent=0, skipped=3, failed=0))
            self.assertEqual(post.call_count, 3)
        self.assertEqual(VehicleNotificationLog.query.filter_by(delivered=True).count(), 3)

    def test_http_error_is_private_and_not_retried_today(self):
        with patch('app.services.telegram_bot_service.requests.post', side_effect=requests.Timeout('secret-token')) as post:
            with self.assertLogs(self.app.logger, level='WARNING') as log:
                self.assertEqual(notifications.send_due_notifications()['failed'], 3)
            self.assertNotIn('secret-token', str(log.output))
            self.assertEqual(notifications.send_due_notifications()['skipped'], 3)
            self.assertEqual(post.call_count, 3)
        self.assertEqual(VehicleNotificationLog.query.filter_by(delivered=False).count(), 3)

    def test_api_false_is_failure(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'ok': False}
        with patch('app.services.telegram_bot_service.requests.post', return_value=response):
            self.assertEqual(notifications.send_due_notifications()['failed'], 3)
        self.assertEqual(VehicleNotificationLog.query.filter_by(delivered=False).count(), 3)
