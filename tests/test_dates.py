import argparse
import unittest
from datetime import datetime, timedelta, timezone

from tests.helpers import *  # noqa: F401,F403  (sets sys.path)
from git_giveback.dates import chunks, format_range, parse_api_datetime, parse_when, resolve_range, to_utc_iso

UTC = timezone.utc
EST = timezone(timedelta(hours=-5), "EST")


class DatesTest(unittest.TestCase):
    def test_plain_date_start_and_inclusive_end(self):
        self.assertEqual(parse_when("2026-03-01", EST), datetime(2026, 3, 1, tzinfo=EST))
        self.assertEqual(parse_when("2026-03-01", EST, end=True), datetime(2026, 3, 2, tzinfo=EST))

    def test_datetime_naive_gets_tz_and_aware_kept(self):
        self.assertEqual(parse_when("2026-03-01T10:30", EST), datetime(2026, 3, 1, 10, 30, tzinfo=EST))
        self.assertEqual(parse_when("2026-03-01T10:30:00Z", EST).utcoffset(), timedelta(0))

    def test_invalid(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_when("March 1", UTC)

    def test_resolve_range_defaults(self):
        now = datetime(2026, 6, 30, 12, tzinfo=UTC)
        self.assertEqual(resolve_range(None, None, None, UTC, now), (now - timedelta(days=30), now))
        self.assertEqual(resolve_range(None, None, 7, UTC, now)[0], now - timedelta(days=7))
        start, end = resolve_range(None, "2026-01-31", 10, UTC, now)
        self.assertEqual((start, end), (datetime(2026, 1, 22, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)))

    def test_resolve_range_rejects_backwards(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            resolve_range("2026-02-01", "2026-01-01", None, UTC)

    def test_chunks(self):
        start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC)
        parts = list(chunks(start, end, 90))
        self.assertEqual(parts[0][0], start)
        self.assertEqual(parts[-1][1], end)
        self.assertTrue(all(b - a <= timedelta(days=90) for a, b in parts))
        self.assertTrue(all(parts[i][1] == parts[i + 1][0] for i in range(len(parts) - 1)))

    def test_iso_helpers(self):
        self.assertEqual(to_utc_iso(datetime(2026, 1, 1, tzinfo=EST)), "2026-01-01T05:00:00Z")
        self.assertEqual(parse_api_datetime("2026-01-01T05:00:00.123Z").tzinfo, UTC)
        self.assertIsNone(parse_api_datetime(None))

    def test_format_range(self):
        text = format_range(datetime(2026, 1, 1, tzinfo=EST), datetime(2026, 7, 1, tzinfo=EST), EST)
        self.assertEqual(text, "2026-01-01 to 2026-06-30 (EST)")
