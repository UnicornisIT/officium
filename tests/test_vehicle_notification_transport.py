"""Offline checks exercise the real Telegram adapter down to its HTTP boundary."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from threading import Barrier
from unittest.mock import Mock, patch

import pytest
import requests
import test_vehicle_service
from flask import Flask
from sqlalchemy import event

from app.models import (
    ActivityLog,
    User,
    VehicleDocument,
    VehicleMaintenanceItem,
    VehicleNotificationLog,
)
from app.services import (
    vehicle_service as vehicles,
    vehicle_notification_service as notifications,
)
from extensions import db


class NotificationTransportTests(unittest.TestCase):
    tearDown = test_vehicle_service.VehicleServiceTestCase.tearDown
    vehicle = test_vehicle_service.VehicleServiceTestCase.vehicle

    def setUp(self):
        test_vehicle_service.VehicleServiceTestCase.setUp(self)
        self.app.config.update(
            VEHICLE_NOTIFICATIONS_ENABLED=True,
            OFFICIUM_PUBLIC_URL='https://example.test',
            TELEGRAM_BOT_TOKEN='offline-test-token',
        )
        self.owner.notification_delivery_time = '00:00'
        vehicle = self.vehicle()
        day = vehicles.today()
        self.now = datetime.combine(day, time(10), timezone.utc)
        db.session.add_all(
            [
                VehicleDocument(
                    vehicle_id=vehicle.id,
                    kind='osago',
                    expires_at=day + timedelta(days=13),
                ),
                VehicleMaintenanceItem(
                    vehicle_id=vehicle.id,
                    name='Масло',
                    interval_months=1,
                    last_done_date=day - timedelta(days=90),
                    is_active=True,
                ),
            ]
        )
        db.session.commit()

    def test_real_adapter_payload_and_dedup(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'ok': True}
        with patch(
            'app.services.telegram_transport.requests.Session.post',
            return_value=response,
        ) as post:
            self.assertEqual(
                self.client.post('/internal/vehicle-notifications/run').json,
                dict(sent=3, skipped=0, failed=0),
            )
            self.assertEqual(post.call_count, 3)
            for call in post.call_args_list:
                self.assertEqual(
                    call.args[0],
                    'https://api.telegram.org/botoffline-test-token/sendMessage',
                )
                self.assertEqual(call.kwargs['json']['chat_id'], self.owner.telegram_id)
                self.assertIn(
                    'https://example.test/vehicles', call.kwargs['json']['text']
                )
                self.assertEqual(call.kwargs['timeout'], 10)
            self.assertEqual(
                self.client.post('/internal/vehicle-notifications/run').json,
                dict(sent=0, skipped=3, failed=0),
            )
            self.assertEqual(post.call_count, 3)
        self.assertEqual(
            VehicleNotificationLog.query.filter_by(delivered=True).count(), 3
        )

    def test_http_error_is_private_and_not_retried_today(self):
        with patch(
            'app.services.telegram_transport.requests.Session.post',
            side_effect=requests.Timeout('secret-token'),
        ) as post:
            with self.assertLogs(self.app.logger, level='WARNING') as log:
                self.assertEqual(notifications.send_due_notifications()['failed'], 3)
            self.assertNotIn('secret-token', str(log.output))
            self.assertEqual(notifications.send_due_notifications()['skipped'], 3)
            self.assertEqual(post.call_count, 3)
        self.assertEqual(
            VehicleNotificationLog.query.filter_by(delivered=False).count(), 3
        )
        self.assertEqual(
            VehicleNotificationLog.query.filter_by(
                status='unknown', attempt_count=1
            ).count(),
            3,
        )

    def test_api_false_is_failure(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'ok': False}
        with patch(
            'app.services.telegram_transport.requests.Session.post',
            return_value=response,
        ):
            self.assertEqual(notifications.send_due_notifications()['failed'], 3)
        self.assertEqual(
            VehicleNotificationLog.query.filter_by(delivered=False).count(), 3
        )

    def test_rate_limited_messages_are_retried_after_retry_after(self):
        now = self.now
        limited = Mock(status_code=429)
        limited.json.return_value = {'ok': False, 'parameters': {'retry_after': 120}}
        accepted = Mock(status_code=200)
        accepted.json.return_value = {'ok': True}
        with patch(
            'app.services.telegram_transport.requests.Session.post',
            side_effect=[limited] * 3 + [accepted] * 3,
        ) as post:
            self.assertEqual(notifications.send_due_notifications(now)['failed'], 3)
            self.assertEqual(
                notifications.send_due_notifications(now + timedelta(seconds=119))[
                    'skipped'
                ],
                3,
            )
            self.assertEqual(
                notifications.send_due_notifications(now + timedelta(seconds=120))[
                    'sent'
                ],
                3,
            )
            self.assertEqual(
                notifications.send_due_notifications(now + timedelta(minutes=5))[
                    'skipped'
                ],
                3,
            )
        self.assertEqual(post.call_count, 6)
        self.assertEqual(
            VehicleNotificationLog.query.filter_by(
                status='accepted', attempt_count=2
            ).count(),
            3,
        )
        self.assertEqual(
            ActivityLog.query.filter_by(action='vehicle_notification_send').count(), 6
        )

    def test_confirmed_rejections_stop_at_attempt_limit(self):
        limited = Mock(status_code=429)
        limited.json.return_value = {'ok': False}
        with patch(
            'app.services.telegram_transport.requests.Session.post',
            return_value=limited,
        ) as post:
            for minute in range(3):
                self.assertEqual(
                    notifications.send_due_notifications(
                        self.now + timedelta(minutes=minute)
                    )['failed'],
                    3,
                )
            self.assertEqual(
                notifications.send_due_notifications(self.now + timedelta(minutes=3))[
                    'skipped'
                ],
                3,
            )
        self.assertEqual(post.call_count, 9)
        self.assertEqual(
            VehicleNotificationLog.query.filter_by(
                status='failed', attempt_count=3, retry_at=None
            ).count(),
            3,
        )

    def test_blocked_chat_is_a_terminal_failure(self):
        blocked = Mock(status_code=403)
        blocked.json.return_value = {'ok': False}
        with patch(
            'app.services.telegram_transport.requests.Session.post',
            return_value=blocked,
        ) as post:
            self.assertEqual(
                notifications.send_due_notifications(self.now)['failed'], 3
            )
            self.assertEqual(
                notifications.send_due_notifications(self.now + timedelta(minutes=5))[
                    'skipped'
                ],
                3,
            )
        self.assertEqual(post.call_count, 3)
        self.assertEqual(
            VehicleNotificationLog.query.filter_by(
                status='failed', attempt_count=1, retry_at=None
            ).count(),
            3,
        )

    def test_interrupted_send_expires_to_unknown_without_replay(self):
        with patch.object(notifications, 'send_to_user', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                notifications.send_due_notifications(self.now)
        row = VehicleNotificationLog.query.one()
        self.assertEqual(row.status, 'reserved')
        notification_id = row.id
        db.session.remove()

        with patch.object(notifications, 'send_to_user', return_value=True) as sender:
            before_expiry = (
                self.now + notifications.RESERVATION_TIMEOUT - timedelta(seconds=1)
            )
            self.assertEqual(
                notifications.send_due_notifications(before_expiry),
                dict(sent=2, failed=0, skipped=1),
            )
            self.assertEqual(
                notifications.send_due_notifications(
                    before_expiry + timedelta(seconds=1)
                ),
                dict(sent=0, failed=1, skipped=3),
            )
            self.assertEqual(sender.call_count, 2)
        row = db.session.get(VehicleNotificationLog, notification_id)
        self.assertEqual(
            (row.status, row.delivered, row.attempt_count), ('unknown', False, 1)
        )
        self.assertEqual(
            ActivityLog.query.filter_by(
                action='vehicle_notification_send', entity_id=row.id
            )
            .one()
            .description,
            'attempt=1 status=unknown failure_kind=reservation_expired',
        )


@pytest.mark.parametrize('retry', [False, True])
def test_workers_compete_for_the_same_delivery(tmp_path, retry):
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        f'sqlite:///{(tmp_path / "workers.sqlite").as_posix()}'
    )
    db.init_app(app)
    now = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
    notice = dict(
        kind='mileage_due', vehicle_id=None, payload_hash='test', text='test', url=None
    )
    with app.app_context():
        db.create_all()
        engine = db.engine
        user = User(
            telegram_id=70001,
            vehicle_notifications_enabled=True,
            notification_timezone='UTC',
            notification_delivery_time='00:00',
        )
        db.session.add(user)
        db.session.flush()
        if retry:
            db.session.add(
                VehicleNotificationLog(
                    user_id=user.id,
                    kind=notice['kind'],
                    payload_hash=notice['payload_hash'],
                    status='failed',
                    attempt_count=1,
                    retry_at=now,
                )
            )
        db.session.commit()

    barrier = Barrier(2)

    def wait_after_read(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        if statement.startswith(
            'SELECT vehicle_notification_log.id AS vehicle_notification_log_id, vehicle_notification_log.attempt_count'
        ):
            barrier.wait(timeout=10)

    def run():
        with app.app_context():
            return notifications.send_due_notifications(now)

    event.listen(engine, 'after_cursor_execute', wait_after_read)
    try:
        with (
            patch.object(notifications, 'notifications_enabled', return_value=True),
            patch.object(notifications, 'due_notifications', return_value=[notice]),
            patch.object(notifications, 'send_to_user', return_value=True) as sender,
            ThreadPoolExecutor(max_workers=2) as workers,
        ):
            results = list(workers.map(lambda _: run(), range(2)))
        assert sum(result['sent'] for result in results) == 1
        assert sum(result['skipped'] for result in results) == 1
        sender.assert_called_once()
        with app.app_context():
            row = VehicleNotificationLog.query.one()
            assert (row.status, row.attempt_count) == ('accepted', 2 if retry else 1)
    finally:
        engine.dispose()
