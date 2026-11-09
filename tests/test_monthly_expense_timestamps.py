import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.services import monthly_expenses_service
from app.services.monthly_expenses_service import _pick_settings_expense


class MonthlyExpenseTimestampTestCase(unittest.TestCase):
    def test_settings_snapshot_accepts_mixed_naive_and_aware_timestamps(self):
        # Rows loaded from the database are naive UTC, while a value assigned in
        # the current session is timezone-aware until the session is expired.
        loaded = SimpleNamespace(
            id=1,
            expense_date=date(2026, 5, 1),
            monthly_settings_updated_at=datetime(2026, 5, 1, 12, 0),
        )
        edited = SimpleNamespace(
            id=2,
            expense_date=date(2026, 5, 2),
            monthly_settings_updated_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=1),
        )

        self.assertIs(_pick_settings_expense([loaded, edited]), edited)

    def test_today_without_app_context_uses_utc(self):
        fixed_now = datetime(2026, 5, 31, 22, 30, tzinfo=timezone.utc)

        with patch('app.time_utils.utc_now', return_value=fixed_now):
            self.assertEqual(monthly_expenses_service._today(), date(2026, 5, 31))


    def test_utc_does_not_depend_on_tzdata(self):
        with patch('app.time_utils.ZoneInfo', side_effect=AssertionError('tzdata lookup')):
            self.assertEqual(
                monthly_expenses_service._today(),
                datetime.now(timezone.utc).date(),
            )


if __name__ == '__main__':
    unittest.main()
