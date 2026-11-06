import unittest
from unittest.mock import patch

from flask import Flask
from flask_wtf.csrf import CSRFProtect

from app.routes import vehicle_notifications


class NotificationLoopbackTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config["TESTING"] = True
        vehicle_notifications.init_app(app, CSRFProtect(app))
        self.client = app.test_client()
        self.path = "/internal/vehicle-notifications/run"
        enabled = patch.object(
            vehicle_notifications, "notifications_enabled", return_value=True
        )
        self.enabled = enabled.start()
        self.addCleanup(enabled.stop)
        sender = patch.object(
            vehicle_notifications,
            "send_due_notifications",
            return_value=dict(sent=0, skipped=0, failed=0),
        )
        self.sender = sender.start()
        self.addCleanup(sender.stop)

    def test_loopback_addresses_are_allowed(self):
        for address in ("127.0.0.1", "127.0.0.2", "::1", "0:0:0:0:0:0:0:1"):
            with self.subTest(address=address):
                self.sender.reset_mock()
                response = self.client.post(
                    self.path,
                    environ_overrides={"REMOTE_ADDR": address},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json, dict(sent=0, skipped=0, failed=0))
                self.sender.assert_called_once_with()

    def test_non_loopback_and_invalid_addresses_are_rejected(self):
        for address in ("198.51.100.2", "2001:db8::1", "::", "", None, "localhost"):
            with self.subTest(address=address):
                response = self.client.post(
                    self.path,
                    environ_overrides={"REMOTE_ADDR": address},
                    headers={"X-Forwarded-For": "::1"},
                )
                self.assertEqual(response.status_code, 403)
        self.sender.assert_not_called()

    def test_disabled_notifications_remain_unavailable_on_ipv6(self):
        self.enabled.return_value = False
        response = self.client.post(
            self.path,
            environ_overrides={"REMOTE_ADDR": "::1"},
        )
        self.assertEqual(response.status_code, 404)
        self.sender.assert_not_called()
