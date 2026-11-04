import unittest
from html.parser import HTMLParser

from app import create_app
from app.models import User
from extensions import db


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.monthly_form = None
        self.form = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.form = attrs
        elif tag == "input" and attrs.get("value") == "monthly_reminders":
            self.monthly_form = self.form


class DisableRemindersTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            dict(
                TESTING=True,
                SECRET_KEY="monthly-reminder-settings-test",
                SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                SQLALCHEMY_ENGINE_OPTIONS={},
                WTF_CSRF_ENABLED=False,
            )
        )
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.user = User(
            telegram_id=101,
            reminder_enabled=True,
            timezone="Europe/Moscow",
            reminder_time="09:30",
            reminder_days_before=3,
        )
        db.session.add(self.user)
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user.id)
            session["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def assert_saved_schedule(self):
        db.session.refresh(self.user)
        self.assertEqual(
            (
                self.user.timezone,
                self.user.reminder_time,
                self.user.reminder_days_before,
            ),
            ("Europe/Moscow", "09:30", 3),
        )

    def test_disable_without_valid_schedule(self):
        for enabled in (None, "off"):
            for schedule in (
                {},
                dict(reminder_timezone="", reminder_time="", reminder_days_before=""),
                dict(
                    reminder_timezone="Not/AZone",
                    reminder_time="24:00",
                    reminder_days_before="8",
                ),
            ):
                with self.subTest(enabled=enabled, schedule=schedule):
                    self.user.reminder_enabled = True
                    db.session.commit()
                    data = dict(section="monthly_reminders", **schedule)
                    if enabled is not None:
                        data["reminder_enabled"] = enabled
                    response = self.client.post("/profile", data=data)
                    self.assertEqual(response.status_code, 302)
                    self.assert_saved_schedule()
                    self.assertFalse(self.user.reminder_enabled)
                    html = self.client.get(response.location).get_data(as_text=True)
                    self.assertIn("Сохранено", html)
                    self.assertNotIn(
                        'class="alert alert-danger py-2 mb-0" role="alert"', html
                    )

    def test_reenable_requires_valid_schedule(self):
        self.assertEqual(
            self.client.post(
                "/profile", data=dict(section="monthly_reminders")
            ).status_code,
            302,
        )
        data = dict(
            section="monthly_reminders",
            reminder_enabled="on",
            reminder_timezone="UTC",
            reminder_time="10:00",
            reminder_days_before="7",
        )
        for key, value in (
            ("reminder_timezone", ""),
            ("reminder_timezone", "Not/AZone"),
            ("reminder_time", ""),
            ("reminder_time", "24:00"),
            ("reminder_days_before", ""),
            ("reminder_days_before", "8"),
        ):
            with self.subTest(key=key, value=value):
                response = self.client.post("/profile", data={**data, key: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(
                    'class="alert alert-danger py-2 mb-0" role="alert"',
                    response.get_data(as_text=True),
                )
                self.assert_saved_schedule()
                self.assertFalse(self.user.reminder_enabled)
        self.assertEqual(self.client.post("/profile", data=data).status_code, 302)
        db.session.refresh(self.user)
        self.assertTrue(self.user.reminder_enabled)
        self.assertEqual(
            (
                self.user.timezone,
                self.user.reminder_time,
                self.user.reminder_days_before,
            ),
            ("UTC", "10:00", 7),
        )

    def test_monthly_form_defers_validation_to_server(self):
        parser = FormParser()
        parser.feed(self.client.get("/profile").get_data(as_text=True))
        self.assertIsNotNone(parser.monthly_form)
        self.assertIn("novalidate", parser.monthly_form)
