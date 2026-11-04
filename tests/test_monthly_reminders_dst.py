from datetime import date, datetime, time, timedelta, timezone
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services import monthly_reminders as reminders
from extensions import db
import test_monthly_reminders


class MonthlyReminderSchedulingTests(unittest.TestCase):
    def test_nine_oclock_is_valid_and_monotonic_across_transitions(self):
        zone = ZoneInfo("Europe/Berlin")
        for transition in (date(2024, 3, 31), date(2024, 10, 27)):
            instants = []
            for offset in (-1, 0, 1):
                day = transition + timedelta(days=offset)
                scheduled = reminders._scheduled_utc(day, time(9), zone)
                local = scheduled.astimezone(zone)
                self.assertEqual(local.date(), day)
                self.assertEqual(local.time(), time(9))
                self.assertIsNotNone(local.utcoffset())
                instants.append(scheduled)
            self.assertLess(instants[0], instants[1])
            self.assertLess(instants[1], instants[2])

    def test_gap_advances_to_first_valid_minute(self):
        zone = ZoneInfo("Europe/Berlin")
        scheduled = reminders._scheduled_utc(date(2024, 3, 31), time(2, 30), zone)
        self.assertEqual(scheduled, datetime(2024, 3, 31, 1, tzinfo=timezone.utc))
        self.assertEqual(scheduled.astimezone(zone).time(), time(3))

    def test_fold_uses_first_occurrence(self):
        zone = ZoneInfo("Europe/Berlin")
        scheduled = reminders._scheduled_utc(date(2024, 10, 27), time(2, 30), zone)
        self.assertEqual(scheduled, datetime(2024, 10, 27, 0, 30, tzinfo=timezone.utc))
        self.assertEqual(scheduled.astimezone(zone).fold, 0)


class MonthlyReminderDeliveryDSTTests(unittest.TestCase):
    setUp = test_monthly_reminders.MonthlyRemindersTests.setUp
    tearDown = test_monthly_reminders.MonthlyRemindersTests.tearDown
    login = test_monthly_reminders.MonthlyRemindersTests.login
    expense = test_monthly_reminders.MonthlyRemindersTests.expense

    def configure_reminder(self, day):
        self.expense(day=day)
        self.user.timezone = "Europe/Berlin"
        self.user.reminder_time = "02:30"
        self.user.reminder_days_before = 0
        db.session.commit()

    def test_gap_delivers_at_first_valid_minute_once(self):
        self.configure_reminder(31)
        with patch.object(
            reminders.telegram, "send_telegram_message", return_value=True
        ) as sender:
            self.assertEqual(
                reminders.run(datetime(2024, 3, 31, 0, 59, tzinfo=timezone.utc)), 0
            )
            self.assertEqual(
                reminders.run(datetime(2024, 3, 31, 1, tzinfo=timezone.utc)), 1
            )
            self.assertEqual(
                reminders.run(datetime(2024, 3, 31, 1, 30, tzinfo=timezone.utc)), 0
            )
        sender.assert_called_once()

    def test_fold_delivers_once_across_both_occurrences(self):
        self.configure_reminder(27)
        with patch.object(
            reminders.telegram, "send_telegram_message", return_value=True
        ) as sender:
            self.assertEqual(
                reminders.run(datetime(2024, 10, 27, 0, 29, tzinfo=timezone.utc)), 0
            )
            self.assertEqual(
                reminders.run(datetime(2024, 10, 27, 0, 30, tzinfo=timezone.utc)), 1
            )
            self.assertEqual(
                reminders.run(datetime(2024, 10, 27, 1, 30, tzinfo=timezone.utc)), 0
            )
        sender.assert_called_once()

    def test_fold_delivery_window_uses_elapsed_utc_hours(self):
        self.configure_reminder(27)
        with patch.object(
            reminders.telegram, "send_telegram_message", return_value=True
        ) as sender:
            self.assertEqual(
                reminders.run(datetime(2024, 10, 27, 6, 31, tzinfo=timezone.utc)), 0
            )
            self.assertEqual(
                reminders.run(datetime(2024, 10, 27, 6, 30, tzinfo=timezone.utc)), 1
            )
        sender.assert_called_once()
