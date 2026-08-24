"""Tests fuer tradingbot/orb_strategy.py gegen trading-bot-spec.md Abschnitt 1."""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.orb_strategy import build_signal, check_breakout, detect_opening_range
from tradingbot.setup_detection import Bar, Direction

T0 = datetime(2026, 1, 2, 9, 30)


def bar(minute_offset: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(T0 + timedelta(minutes=5 * minute_offset), o, h, low, c)


OR_BARS_DATA = [
    bar(0, 100.0, 100.5, 99.5, 100.2),
    bar(1, 100.2, 100.8, 100.0, 100.6),
    bar(2, 100.6, 100.9, 100.3, 100.4),
    bar(3, 100.4, 100.7, 100.1, 100.5),
    bar(4, 100.5, 101.0, 100.2, 100.9),  # Tageshoch 101.0
    bar(5, 100.9, 101.1, 99.0, 100.3),   # Tagestief 99.0
]


class OpeningRangeTests(unittest.TestCase):
    def test_none_before_six_bars(self):
        self.assertIsNone(detect_opening_range(OR_BARS_DATA[:5]))

    def test_range_from_first_six_bars(self):
        rng = detect_opening_range(OR_BARS_DATA)
        self.assertIsNotNone(rng)
        self.assertAlmostEqual(rng.high, 101.1)
        self.assertAlmostEqual(rng.low, 99.0)
        self.assertEqual(rng.day, T0.date())

    def test_seventh_bar_does_not_widen_range(self):
        # Kerze 6 (105.0/90.0) liegt ausserhalb der ersten 6 Kerzen und
        # darf die Spanne nicht beeinflussen, auch wenn sie mituebergeben wird
        extra = OR_BARS_DATA + [bar(6, 100.3, 105.0, 90.0, 100.3)]
        rng = detect_opening_range(extra)
        self.assertAlmostEqual(rng.high, 101.1)
        self.assertAlmostEqual(rng.low, 99.0)


class BreakoutTests(unittest.TestCase):
    def setUp(self):
        self.rng = detect_opening_range(OR_BARS_DATA)

    def test_close_above_range_is_long_breakout(self):
        candle = bar(6, 101.0, 101.3, 100.9, 101.2)  # close 101.2 > 101.1
        self.assertIs(check_breakout(self.rng, candle), Direction.LONG)

    def test_close_below_range_is_short_breakout(self):
        candle = bar(6, 99.2, 99.3, 98.5, 98.8)  # close 98.8 < 99.0
        self.assertIs(check_breakout(self.rng, candle), Direction.SHORT)

    def test_close_inside_range_is_no_breakout(self):
        candle = bar(6, 100.0, 100.5, 99.8, 100.2)
        self.assertIsNone(check_breakout(self.rng, candle))

    def test_close_exactly_at_boundary_is_no_breakout(self):
        # spec verlangt echtes Ueberschreiten (>), nicht Erreichen (>=)
        candle = bar(6, 100.5, 101.1, 100.0, 101.1)
        self.assertIsNone(check_breakout(self.rng, candle))


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.rng = detect_opening_range(OR_BARS_DATA)

    def test_long_signal_uses_entry_bar_open_and_opposite_side_stop(self):
        entry_bar = bar(7, 101.5, 101.6, 101.4, 101.55)
        signal = build_signal(Direction.LONG, self.rng, entry_bar)

        self.assertIs(signal.direction, Direction.LONG)
        self.assertAlmostEqual(signal.entry_price, 101.5)  # Open der Einstiegskerze
        self.assertAlmostEqual(signal.stop, 99.0)  # Gegenseite der Spanne
        self.assertAlmostEqual(signal.risk, 2.5)
        self.assertAlmostEqual(signal.target, 101.5 + 2.0 * 2.5)  # 2:1 CRV

    def test_short_signal_uses_entry_bar_open_and_opposite_side_stop(self):
        entry_bar = bar(7, 98.7, 98.8, 98.6, 98.65)
        signal = build_signal(Direction.SHORT, self.rng, entry_bar)

        self.assertIs(signal.direction, Direction.SHORT)
        self.assertAlmostEqual(signal.entry_price, 98.7)
        self.assertAlmostEqual(signal.stop, 101.1)
        self.assertAlmostEqual(signal.risk, 2.4)
        self.assertAlmostEqual(signal.target, 98.7 - 2.0 * 2.4)


if __name__ == "__main__":
    unittest.main()
