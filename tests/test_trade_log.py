"""Tests fuer tradingbot/trade_log.py."""

import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.setup_detection import Direction
from tradingbot.trade_log import COLUMNS, TradeLogRow, append_trade


def make_row(**overrides) -> TradeLogRow:
    defaults = dict(
        timestamp=datetime(2026, 8, 20, 10, 5),
        direction=Direction.LONG,
        level=500.0,
        entry_planned=500.10,
        entry_actual=500.15,
        stop=498.0,
        target=504.2,
        qty=10,
        risk=2.1,
        exit_reason="target",
        exit_price=504.2,
        pnl=41.0,
        duration_minutes=35.0,
        run_delay_minutes=1.5,
    )
    defaults.update(overrides)
    return TradeLogRow(**defaults)


class SlippageTests(unittest.TestCase):
    def test_long_worse_fill_is_positive_slippage(self):
        row = make_row(direction=Direction.LONG, entry_planned=500.0, entry_actual=500.10)
        self.assertAlmostEqual(row.slippage, 0.10)

    def test_long_better_fill_is_negative_slippage(self):
        row = make_row(direction=Direction.LONG, entry_planned=500.0, entry_actual=499.95)
        self.assertAlmostEqual(row.slippage, -0.05)

    def test_short_worse_fill_is_positive_slippage(self):
        # Short: schlechter = zu niedrig verkauft
        row = make_row(direction=Direction.SHORT, entry_planned=500.0, entry_actual=499.90)
        self.assertAlmostEqual(row.slippage, 0.10)

    def test_short_better_fill_is_negative_slippage(self):
        row = make_row(direction=Direction.SHORT, entry_planned=500.0, entry_actual=500.05)
        self.assertAlmostEqual(row.slippage, -0.05)


class PnlInRTests(unittest.TestCase):
    def test_pnl_in_r_matches_risk_times_qty(self):
        row = make_row(risk=2.0, qty=10, pnl=30.0)  # Gesamtrisiko 20 -> 1.5R
        self.assertAlmostEqual(row.pnl_in_r, 1.5)

    def test_zero_risk_does_not_raise(self):
        row = make_row(risk=0.0, qty=10, pnl=5.0)
        self.assertEqual(row.pnl_in_r, 0.0)


class AppendTradeTests(unittest.TestCase):
    def test_writes_header_once_and_appends_rows(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.csv"
            append_trade(path, make_row())
            append_trade(path, make_row(pnl=-10.0))

            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(lines[0], ",".join(COLUMNS))
            self.assertEqual(len(lines), 3)  # Header + 2 Zeilen


if __name__ == "__main__":
    unittest.main()
