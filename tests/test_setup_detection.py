"""Tests fuer tradingbot/setup_detection.py gegen die Regeln aus
trading-bot-spec.md, Abschnitt 1.
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.setup_detection import (
    Bar,
    Direction,
    detect_setup,
    detect_setups,
    simulate_entries,
)

T0 = datetime(2026, 1, 2, 9, 30)


def bar(minute_offset: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(T0 + timedelta(minutes=5 * minute_offset), o, h, low, c)


class DetectSetupTests(unittest.TestCase):
    def test_bullish_wickless_candle_is_long_setup(self):
        # open=10.00 close=10.10 low=10.00 high=10.10 -> kein unterer Docht
        b = bar(0, 10.00, 10.10, 10.00, 10.10)
        setup = detect_setup(b, 0)
        self.assertIsNotNone(setup)
        self.assertIs(setup.direction, Direction.LONG)
        self.assertEqual(setup.level, 10.00)
        self.assertAlmostEqual(setup.range, 0.10)

    def test_bearish_wickless_candle_is_short_setup(self):
        b = bar(0, 10.10, 10.10, 10.00, 10.00)
        setup = detect_setup(b, 0)
        self.assertIsNotNone(setup)
        self.assertIs(setup.direction, Direction.SHORT)
        self.assertEqual(setup.level, 10.10)

    def test_bullish_candle_with_too_much_lower_wick_is_no_setup(self):
        # unterer Docht 0.05 bei Range 0.10 -> 50 % > 2 % Toleranz
        b = bar(0, 10.05, 10.10, 10.00, 10.10)
        self.assertIsNone(detect_setup(b, 0))

    def test_bearish_candle_with_too_much_upper_wick_is_no_setup(self):
        b = bar(0, 10.05, 10.10, 10.00, 10.00)
        self.assertIsNone(detect_setup(b, 0))

    def test_doji_is_no_setup(self):
        b = bar(0, 10.05, 10.10, 10.00, 10.05)
        self.assertIsNone(detect_setup(b, 0))

    def test_zero_range_bar_is_no_setup(self):
        b = bar(0, 10.00, 10.00, 10.00, 10.00)
        self.assertIsNone(detect_setup(b, 0))

    def test_wick_just_within_tolerance_counts_as_setup(self):
        # Docht bewusst deutlich unter der 2%-Grenze, um Gleitkomma-Artefakte
        # an der exakten Grenze zu vermeiden (siehe test_bullish_..._is_no_setup
        # fuer den Fall klar ueber der Grenze).
        low, high = 10.00, 10.10
        wick = 0.5 * 0.02 * (high - low)  # 1 % der Range, klar innerhalb der 2%-Toleranz
        b = bar(0, low + wick, high, low, high)
        setup = detect_setup(b, 0)
        self.assertIsNotNone(setup)
        self.assertIs(setup.direction, Direction.LONG)


class SimulateEntriesTests(unittest.TestCase):
    def setUp(self):
        # Kerze 0: Long-Setup bei Level 10.00
        self.setup_bar = bar(0, 10.00, 10.10, 10.00, 10.10)

    def test_entry_triggers_on_retest_within_window(self):
        bars = [self.setup_bar]
        bars += [bar(i, 10.20, 10.25, 10.15, 10.20) for i in range(1, 4)]  # keine Beruehrung
        bars.append(bar(4, 10.10, 10.12, 9.99, 10.05))  # low <= 10.00 -> Trigger

        setups = detect_setups(bars)
        trades = simulate_entries(bars, setups)

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].entry_index, 4)
        self.assertIs(trades[0].direction, Direction.LONG)

    def test_setup_expires_after_ten_candles_without_retest(self):
        bars = [self.setup_bar]
        bars += [bar(i, 10.20, 10.25, 10.15, 10.20) for i in range(1, 12)]  # nie beruehrt

        setups = detect_setups(bars)
        trades = simulate_entries(bars, setups)

        self.assertEqual(len(trades), 0)

    def test_setup_triggers_only_once(self):
        bars = [self.setup_bar]
        # Kerze 1 und Kerze 2 beruehren beide das Level 10.00
        bars.append(bar(1, 10.05, 10.08, 9.98, 10.01))
        bars.append(bar(2, 10.05, 10.08, 9.97, 10.01))

        setups = detect_setups(bars)
        trades = simulate_entries(bars, setups)

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].entry_index, 1)


if __name__ == "__main__":
    unittest.main()
