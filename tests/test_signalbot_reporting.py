"""Tests fuer die reinen Teile von signalbot/reporting.py.
build_signal_status_report selbst braucht die echte cTrader-Verbindung
und wird ueber scripts/run_signal_bot.py/Telegram manuell geprueft
(gleiches Muster wie tests/test_reporting.py fuer den ORB-Bot)."""

import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signalbot.reporting import _money, _recent_trades
from signalbot.trade_log import SignalTradeLogRow, append_trade
from tradingbot.setup_detection import Direction


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
                row = SignalTradeLogRow(
                    timestamp=datetime(2026, 8, i + 1, 10, 0),
                    symbol="NAS100",
                    direction=Direction.LONG,
                    source_message_id=i,
                    entry_planned=500, entry_actual=500.1,
                    stop=498, target=504, qty=0.1, risk=2.0,
                    exit_reason="target", exit_price=504, pnl=i * 5.0,
                    duration_minutes=30,
                )
                append_trade(path, row)

            lines = _recent_trades(path, 3)
            self.assertEqual(len(lines), 3)
            self.assertIn("2026-08-05", lines[0])
            self.assertIn("2026-08-07", lines[-1])
            self.assertIn("NAS100", lines[0])


if __name__ == "__main__":
    unittest.main()
