"""Tests fuer tradingbot/backtest.py gegen die Stop/Ziel/Tagesende-Regeln
aus trading-bot-spec.md, Abschnitt 1.
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.backtest import run_backtest
from tradingbot.setup_detection import Bar

T0 = datetime(2026, 1, 2, 9, 30)


def bar(minute_offset: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(T0 + timedelta(minutes=5 * minute_offset), o, h, low, c)


class LongTradeTests(unittest.TestCase):
    def setUp(self):
        # Kerze 0: Long-Setup, Level 10.00, range 0.10 -> Stop 9.95, Risiko 0.05, Ziel 10.075
        self.setup_bar = bar(0, 10.00, 10.10, 10.00, 10.10)

    def test_target_hit_first(self):
        bars = [self.setup_bar]
        bars.append(bar(1, 10.05, 10.08, 9.99, 10.02))  # Retest -> Entry bei 10.00
        bars.append(bar(2, 10.02, 10.07, 10.01, 10.06))  # beruehrt weder Stop (9.95) noch Ziel (10.075)
        bars.append(bar(3, 10.07, 10.09, 10.06, 10.08))  # High 10.09 >= Ziel 10.075

        [trade] = run_backtest(bars)
        self.assertEqual(trade.exit_reason, "target")
        self.assertAlmostEqual(trade.exit_price, 10.075)
        self.assertAlmostEqual(trade.r_multiple, 1.5)

    def test_stop_hit_first(self):
        bars = [self.setup_bar]
        bars.append(bar(1, 10.05, 10.08, 9.99, 10.02))  # Entry bei 10.00
        bars.append(bar(2, 10.00, 10.01, 9.94, 9.96))  # Low 9.94 <= Stop 9.95

        [trade] = run_backtest(bars)
        self.assertEqual(trade.exit_reason, "stop")
        self.assertAlmostEqual(trade.exit_price, 9.95)
        self.assertAlmostEqual(trade.r_multiple, -1.0)

    def test_both_touched_same_bar_counts_as_stop(self):
        bars = [self.setup_bar]
        bars.append(bar(1, 10.05, 10.08, 9.99, 10.02))  # Entry bei 10.00
        bars.append(bar(2, 10.02, 10.20, 9.90, 10.05))  # beruehrt Stop 9.95 UND Ziel 10.075

        [trade] = run_backtest(bars)
        self.assertEqual(trade.exit_reason, "stop")

    def test_forced_close_at_end_of_day_uses_open_of_last_bar(self):
        bars = [self.setup_bar]
        bars.append(bar(1, 10.05, 10.08, 9.99, 10.02))  # Entry bei 10.00
        # weder Stop (9.95) noch Ziel (10.075) beruehrt, letzte Kerze des Tages:
        bars.append(bar(2, 10.02, 10.04, 10.00, 10.03))

        [trade] = run_backtest(bars)
        self.assertEqual(trade.exit_reason, "eod")
        self.assertAlmostEqual(trade.exit_price, 10.02)  # Open der letzten Kerze

    def test_entry_on_last_bar_of_day_closes_immediately(self):
        bars = [self.setup_bar]
        bars += [bar(i, 10.20, 10.25, 10.15, 10.20) for i in range(1, 4)]
        # letzte Kerze des Tages beruehrt gleichzeitig das Level -> Entry und
        # sofortiger Zwangsschluss ohne weitere Kerze zum Pruefen
        bars.append(bar(4, 10.05, 10.06, 9.99, 10.01))

        [trade] = run_backtest(bars)
        self.assertEqual(trade.exit_reason, "eod")
        self.assertAlmostEqual(trade.exit_price, 10.01)  # Close der Einstiegskerze


class ShortTradeTests(unittest.TestCase):
    def test_short_target_hit(self):
        # Kerze 0: Short-Setup, Level 10.10, range 0.10 -> Stop 10.15, Risiko 0.05, Ziel 10.025
        bars = [bar(0, 10.10, 10.10, 10.00, 10.00)]
        bars.append(bar(1, 10.05, 10.11, 10.03, 10.06))  # High 10.11 >= Level -> Entry bei 10.10
        bars.append(bar(2, 10.06, 10.07, 10.02, 10.03))  # Low 10.02 <= Ziel 10.025

        [trade] = run_backtest(bars)
        self.assertEqual(trade.exit_reason, "target")
        self.assertAlmostEqual(trade.exit_price, 10.025)
        self.assertAlmostEqual(trade.r_multiple, 1.5)


class NoTradesTests(unittest.TestCase):
    def test_empty_bars_produce_no_trades(self):
        self.assertEqual(run_backtest([]), [])


if __name__ == "__main__":
    unittest.main()
