from datetime import datetime, timezone
import unittest

from app.time_utils import as_utc, utc_now


class TimeUtilsTestCase(unittest.TestCase):
    def test_utc_now_is_timezone_aware(self):
        value = utc_now()

        self.assertIsNotNone(value.tzinfo)
        self.assertEqual(value.utcoffset(), timezone.utc.utcoffset(value))

    def test_as_utc_preserves_legacy_naive_wall_time(self):
        legacy_value = datetime(2026, 10, 9, 12, 30, 0)

        converted = as_utc(legacy_value)

        self.assertEqual(converted.replace(tzinfo=None), legacy_value)
        self.assertEqual(converted.tzinfo, timezone.utc)


if __name__ == '__main__':
    unittest.main()
