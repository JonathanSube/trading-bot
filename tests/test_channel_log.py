"""Tests fuer signalbot/channel_log.py: Protokollierung aller ausgewerteten
Kanal-Nachrichten mit Sieben-Tage-Rotation."""

import csv
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signalbot.channel_log import append_channel_message

UTC = timezone.utc


def utc(y, m, d, h=0) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


def _read_rows(path: Path) -> list[list[str]]:
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # Header
        return list(reader)


class AppendChannelMessageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "signal_channel_log.csv"

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_write_creates_header_and_row(self):
        append_channel_message(self.path, utc(2026, 8, 26), 1, "hallo", None, "kein_signal")
        rows = _read_rows(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "1")
        self.assertEqual(rows[0][4], "kein_signal")

    def test_appends_across_multiple_calls(self):
        append_channel_message(self.path, utc(2026, 8, 26), 1, "erste", None, "kein_signal")
        append_channel_message(self.path, utc(2026, 8, 26, 1), 2, "zweite", {"is_signal": True}, "trade_eroeffnet")
        rows = _read_rows(self.path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "2")

    def test_parsed_dict_is_serialized_as_json(self):
        append_channel_message(self.path, utc(2026, 8, 26), 1, "text",
                                {"is_signal": True, "index": "NASDAQ"}, "trade_eroeffnet")
        rows = _read_rows(self.path)
        self.assertIn('"index": "NASDAQ"', rows[0][3])

    def test_rows_older_than_retention_are_dropped(self):
        append_channel_message(self.path, utc(2026, 8, 1), 1, "alt", None, "kein_signal")
        append_channel_message(self.path, utc(2026, 8, 8, 1), 2, "neu", None, "kein_signal")
        rows = _read_rows(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "2")

    def test_row_exactly_at_retention_boundary_is_kept(self):
        now = utc(2026, 8, 26)
        append_channel_message(self.path, now - timedelta(days=7), 1, "grenze", None, "kein_signal")
        append_channel_message(self.path, now, 2, "aktuell", None, "kein_signal")
        rows = _read_rows(self.path)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
