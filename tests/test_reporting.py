"""Tests fuer die reinen Teile von tradingbot/reporting.py. build_status_report
selbst braucht die echte Alpaca-API und wird ueber run_bot.py/Telegram
manuell geprueft."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.reporting import _money, _recent_trades
from tradingbot.setup_detection import Direction
from tradingbot.trade_log import TradeLogRow, append_trade
from datetime import datetime


class MoneyFormattingTests(unittest.TestCase):
    def test_positive_gets_plus_sign_and_german_separators(self):
        self.assertEqual(_money(100312.4), "+100.312,40")

    def test_negative_keeps_single_minus(self):
        self.assertEqual(_money(-31.1), "-31,10")

    def test_zero_has_no_sign(self):
        self.assertEqual(_money(0.0), "0,00")


class RecentTradesTests(unittest.TestCase):
    def test_missing_file_returns_empty_list(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(_recent_trades(Path(tmp) / "trades.csv", 5), [])

    def test_returns_only_last_n_rows(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.csv"
            for i in range(7):
                row = TradeLogRow(
                    timestamp=datetime(2026, 8, i + 1, 10, 0),
                    direction=Direction.LONG,
                    level=500, entry_planned=500, entry_actual=500.1,
                    stop=498, target=504, qty=10, risk=2.0,
                    exit_reason="target", exit_price=504, pnl=i * 5.0,
                    duration_minutes=30, run_delay_minutes=1.0,
                )
                append_trade(path, row)

            lines = _recent_trades(path, 3)
            self.assertEqual(len(lines), 3)
            self.assertIn("2026-08-05", lines[0])
            self.assertIn("2026-08-07", lines[-1])


if __name__ == "__main__":
    unittest.main()
