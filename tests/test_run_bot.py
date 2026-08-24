"""Tests fuer die reinen Teile von scripts/run_bot.py. Die API-abhaengigen
Funktionen (Order-Abgleich, Order-Platzierung) brauchen die echte
Alpaca-API und werden manuell geprueft, siehe Abschnitt 8 Schritt 4/5.
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_bot import log_and_clear, scheduled_run_time
from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction
from tradingbot.state import BotState
from tradingbot.trade_log import COLUMNS

NY = ZoneInfo("America/New_York")


class ScheduledRunTimeTests(unittest.TestCase):
    def test_rounds_down_to_five_minute_mark(self):
        now = datetime(2026, 8, 20, 10, 7, 32)
        self.assertEqual(scheduled_run_time(now), datetime(2026, 8, 20, 10, 5))

    def test_exact_mark_stays_unchanged(self):
        now = datetime(2026, 8, 20, 10, 5, 0)
        self.assertEqual(scheduled_run_time(now), datetime(2026, 8, 20, 10, 5))

    def test_seconds_and_microseconds_dropped(self):
        now = datetime(2026, 8, 20, 10, 5, 59, 999)
        self.assertEqual(scheduled_run_time(now), datetime(2026, 8, 20, 10, 5))


class LogAndClearTests(unittest.TestCase):
    def test_long_trade_pnl_uses_actual_entry_fill_not_planned(self):
        signal = Signal(Direction.LONG, 100.0, 98.0, 104.0, 2.0, datetime(2026, 8, 20, 10, 5, tzinfo=NY))
        state = BotState(open_trade=signal, open_qty=10, open_entry_fill=100.10)

        with TemporaryDirectory() as tmp:
            import scripts.run_bot as run_bot
            original_path = run_bot.TRADE_LOG_PATH
            run_bot.TRADE_LOG_PATH = Path(tmp) / "trades.csv"
            try:
                now = datetime(2026, 8, 20, 10, 40, tzinfo=NY)
                log_and_clear(state, now, run_delay=1.2, exit_price=104.0, exit_reason="target", level=98.0)
                content = run_bot.TRADE_LOG_PATH.read_text(encoding="utf-8")
            finally:
                run_bot.TRADE_LOG_PATH = original_path

        # pnl = (104.0 - 100.10) * 10 = 39.0, nicht (104.0-100.0)*10=40.0
        self.assertIn("39.00000000", content)  # Gleitkomma, siehe assertAlmostEqual unten
        self.assertAlmostEqual(state.total_pnl, 39.0)
        self.assertIsNone(state.open_trade)
        self.assertIsNone(state.open_entry_fill)

    def test_falls_back_to_planned_entry_if_fill_unknown(self):
        signal = Signal(Direction.SHORT, 100.0, 102.0, 94.0, 2.0, datetime(2026, 8, 20, 10, 5, tzinfo=NY))
        state = BotState(open_trade=signal, open_qty=5, open_entry_fill=None)

        with TemporaryDirectory() as tmp:
            import scripts.run_bot as run_bot
            original_path = run_bot.TRADE_LOG_PATH
            run_bot.TRADE_LOG_PATH = Path(tmp) / "trades.csv"
            try:
                now = datetime(2026, 8, 20, 15, 55, tzinfo=NY)
                log_and_clear(state, now, run_delay=0.5, exit_price=97.0, exit_reason="eod", level=102.0)
            finally:
                run_bot.TRADE_LOG_PATH = original_path

        # Short: pnl = (100.0 - 97.0) * 5 = 15.0
        self.assertEqual(state.total_pnl, 15.0)


if __name__ == "__main__":
    unittest.main()
