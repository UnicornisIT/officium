import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import g
from app import create_app
from app.models import User, VehicleNotificationLog
from app.services import personal_settings_service as settings
from app.services import vehicle_notification_service as notifications, vehicle_service as vehicles
from extensions import db


class PersonalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(dict(TESTING=True, SECRET_KEY='personal-settings-test',
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_ENGINE_OPTIONS={},
            WTF_CSRF_ENABLED=False, VEHICLES_ENABLED=True, VEHICLE_NOTIFICATIONS_ENABLED=True,
            TELEGRAM_BOT_TOKEN='offline-token', TELEGRAM_BOT_USERNAME='officium_test_bot',
            OFFICIUM_PUBLIC_URL='https://example.test', APP_TIMEZONE='Europe/Moscow',
            VEHICLE_NOTIFICATIONS_HOUR=10))
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.user = User(telegram_id=101, username='alice', first_name='Анна')
        self.other = User(telegram_id=102)
        self.google = User(telegram_id=-103, google_id='google-only')
        db.session.add_all([self.user, self.other, self.google])
        db.session.commit()
        self.client = self.app.test_client()
        self.login(self.user)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def login(self, user):
        g.pop('_login_user', None)
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user.id)
            session['_fresh'] = True

    def test_account_and_independent_owner_settings(self):
        html = self.client.get('/profile').get_data(as_text=True)
        for text in ['Аккаунт', 'Уведомления', 'Приватность', '@alice', 'Вход через Telegram', 'Дата регистрации', 'Открыть бота']:
            self.assertIn(text, html)
        self.assertNotIn('Доставка работает', html)
        response = self.client.post('/profile', data=dict(section='notifications',
            notification_timezone='Asia/Vladivostok', notification_delivery_time='08:35',
            vehicle_notifications_enabled='on', user_id=self.other.id))
        self.assertEqual(response.status_code, 302)
        self.assertEqual((self.user.notification_timezone, self.user.notification_delivery_time), ('Asia/Vladivostok', '08:35'))
        self.assertIsNone(self.other.notification_timezone)
        self.assertIn('Сохранено', self.client.get('/profile').get_data(as_text=True))
        self.client.post('/profile', data=dict(section='privacy', hide_amounts_on_open='on'))
        self.assertTrue(self.user.hide_amounts_on_open)
        self.assertTrue(self.user.vehicle_notifications_enabled)
        self.assertEqual(self.user.notification_delivery_time, '08:35')
        self.assertFalse(self.other.hide_amounts_on_open)

    def test_invalid_settings_are_atomic(self):
        for zone, clock in [('UTC+3', '09:00'), ('Not/AZone', '09:00'), ('UTC', '24:00'), ('UTC', '1:00'), ('UTC', '12:60')]:
            with self.subTest(zone=zone, clock=clock):
                response = self.client.post('/profile', data=dict(section='notifications',
                    notification_timezone=zone, notification_delivery_time=clock))
                self.assertEqual(response.status_code, 400)
                self.assertIsNone(self.user.notification_timezone)
                self.assertTrue(self.user.vehicle_notifications_enabled)

    def test_without_telegram_and_dev_account(self):
        self.login(self.google)
        html = self.client.get('/profile').get_data(as_text=True)
        self.assertIn('не связан с Telegram', html)
        self.assertNotIn('Проверить уведомление', html)
        self.assertEqual(self.client.post('/profile', data=dict(section='notifications')).status_code, 403)
        self.assertEqual(self.client.post('/profile/test-notification').status_code, 403)
        self.assertEqual(self.client.post('/profile', data=dict(section='privacy', hide_amounts_on_open='on')).status_code, 302)
        self.assertTrue(self.google.hide_amounts_on_open)
        self.google.google_id = None
        db.session.commit()
        self.assertIn('Локальный dev-аккаунт', self.client.get('/profile').get_data(as_text=True))

    def test_csrf_required_for_settings_and_test(self):
        self.app.config['WTF_CSRF_ENABLED'] = True
        with patch.object(settings.telegram, 'send_telegram_message') as sender:
            self.assertEqual(self.client.post('/profile', data=dict(section='privacy', hide_amounts_on_open='on')).status_code, 400)
            self.assertEqual(self.client.post('/profile/test-notification').status_code, 400)
            sender.assert_not_called()
        self.assertFalse(self.user.hide_amounts_on_open)

    def test_test_delivery_explicit_rate_limited_and_isolated(self):
        with patch.object(settings.telegram, 'send_telegram_message', return_value=True) as sender:
            self.client.get('/profile')
            sender.assert_not_called()
            self.assertEqual(self.client.get('/profile/test-notification').status_code, 405)
            response = self.client.post('/profile/test-notification', data=dict(user_id=self.other.id))
            self.assertEqual(response.status_code, 303)
            self.assertEqual(sender.call_args.args[0], self.user.telegram_id)
            self.assertEqual(self.client.post('/profile/test-notification').status_code, 429)
            self.assertEqual(sender.call_count, 1)
            self.login(self.other)
            self.assertEqual(self.client.post('/profile/test-notification').status_code, 303)
            self.assertEqual(sender.call_count, 2)

    def test_delivery_errors_and_no_token_are_honest(self):
        self.app.config['TELEGRAM_BOT_TOKEN'] = ''
        self.assertEqual(self.client.post('/profile/test-notification').status_code, 503)
        self.assertIsNone(self.user.notification_test_requested_at)
        self.app.config['TELEGRAM_BOT_TOKEN'] = 'offline-token'
        with patch.object(settings.telegram, 'send_telegram_message', side_effect=RuntimeError('SECRET_TOKEN')):
            response = self.client.post('/profile/test-notification')
            self.assertEqual(response.status_code, 502)
            self.assertNotIn(b'SECRET_TOKEN', response.data)
            self.assertEqual(self.client.post('/profile/test-notification').status_code, 429)
        self.user.notification_test_requested_at -= timedelta(seconds=61)
        db.session.commit()
        with patch.object(settings.telegram, 'send_telegram_message', return_value=False):
            self.assertEqual(self.client.post('/profile/test-notification').status_code, 502)

    def test_legacy_schedule_and_dst_dedup(self):
        vehicles.create_vehicle(self.user, dict(name='Машина'))
        self.assertEqual(settings.delivery_preferences(self.user), ('Europe/Moscow', '10:00'))
        with patch.object(notifications, 'send_to_user', return_value=True) as sender:
            self.assertEqual(notifications.send_due_notifications(datetime(2026, 11, 3, 6, 59, tzinfo=timezone.utc))['sent'], 0)
            self.assertEqual(notifications.send_due_notifications(datetime(2026, 11, 3, 7, tzinfo=timezone.utc))['sent'], 1)
            self.assertEqual(notifications.send_due_notifications(datetime(2026, 11, 3, 7, 5, tzinfo=timezone.utc))['skipped'], 1)
            self.user.notification_timezone = 'America/New_York'
            self.user.notification_delivery_time = '02:30'
            db.session.commit()
            # Spring forward skips 02:30: send at 03:00, once for that local day.
            self.assertEqual(notifications.send_due_notifications(datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc))['sent'], 0)
            self.assertEqual(notifications.send_due_notifications(datetime(2026, 3, 8, 7, tzinfo=timezone.utc))['sent'], 1)
            self.assertEqual(notifications.send_due_notifications(datetime(2026, 3, 8, 7, 5, tzinfo=timezone.utc))['skipped'], 1)
            self.user.notification_delivery_time = '01:30'
            db.session.commit()
            # Autumn repeats 01:30: UTC 05:30 and 06:30 share the same daily key.
            self.assertEqual(notifications.send_due_notifications(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))['sent'], 1)
            self.assertEqual(notifications.send_due_notifications(datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))['skipped'], 1)
            self.assertEqual(sender.call_count, 3)
        self.assertEqual(VehicleNotificationLog.query.count(), 3)

    def test_hidden_on_first_html_and_plain_export_formatter(self):
        self.user.hide_amounts_on_open = True
        db.session.commit()
        from flask import render_template_string
        from flask_login import login_user
        from app.utils import format_currency
        with self.app.test_request_context('/'):
            login_user(self.user)
            html = render_template_string('{{ 9876|money }}')
            self.assertIn('data-private-money', html)
            self.assertIn('money-mask', html)
            self.assertNotIn('<span', format_currency(9876))
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('class="amounts-hidden amounts-pending"', html)
        self.assertIn('id="amountPrivacyToggle"', html)
