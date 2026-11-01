from datetime import timedelta
from unittest.mock import patch
import test_vehicle_notifications
from app.services import vehicle_notification_service as service
from app.models import ActivityLog


class NotificationRunnerTests(test_vehicle_notifications.NotificationTests):
    def test_local_only_and_runner_idempotence(self):
        self.vehicle()
        path='/internal/vehicle-notifications/run'
        self.assertEqual(self.client.post(path,environ_overrides={'REMOTE_ADDR':'198.51.100.2'},headers={'X-Forwarded-For':'127.0.0.1'}).status_code,403)
        with patch.object(service.telegram,'send_telegram_message',return_value=True) as sender:
            first=self.client.post(path)
            self.assertEqual(first.status_code,200)
            self.assertEqual(first.json,dict(sent=1,skipped=0,failed=0))
            self.assertEqual(self.client.post(path).json,dict(sent=0,skipped=1,failed=0))
            self.assertEqual(sender.call_count,1)
        self.assertEqual(ActivityLog.query.filter_by(action='vehicle_notification_send').count(),1)
