"""Tests fuer die reine Positionsgroessen-Berechnung in
tradingbot/ctrader.py. Die eigentlichen API-Aufrufe (Connect/Auth,
place_market_order, get_account_info etc.) brauchen die echte cTrader
Open API und werden manuell geprueft, siehe scripts/find_ctrader_symbols.py
und scripts/place_test_ctrader_order.py.
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.ctrader import position_size
from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction


class PositionSizeTests(unittest.TestCase):
    def test_default_risk_pct(self):
        # 3% von 100.000 / Risiko 2.0 = 1500, auf 0,01-Lot abgerundet -> 1500.0
        signal = Signal(Direction.LONG, 100.0, 98.0, 104.0, 2.0, datetime(2026, 1, 1))
        self.assertEqual(position_size(signal, equity=100_000.0), 1500.0)

    def test_custom_risk_pct(self):
        # 1% von 100.000 / Risiko 2.0 = 500.0 Lot
        signal = Signal(Direction.LONG, 100.0, 98.0, 104.0, 2.0, datetime(2026, 1, 1))
        self.assertEqual(position_size(signal, equity=100_000.0, risk_pct=0.01), 500.0)

    def test_zero_risk_returns_zero(self):
        signal = Signal(Direction.LONG, 100.0, 100.0, 100.0, 0.0, datetime(2026, 1, 1))
        self.assertEqual(position_size(signal, equity=100_000.0), 0.0)

    def test_zero_equity_returns_zero(self):
        signal = Signal(Direction.LONG, 100.0, 98.0, 104.0, 2.0, datetime(2026, 1, 1))
        self.assertEqual(position_size(signal, equity=0.0), 0.0)

    def test_rounds_down_to_hundredth_lot(self):
        signal = Signal(Direction.LONG, 100.0, 97.1, 105.8, 2.9, datetime(2026, 1, 1))
        # 3% von 100.000 / 2.9 = 1034.48... -> 1034.48 (0,01-Lot-Schritt)
        self.assertEqual(position_size(signal, equity=100_000.0), 1034.48)


if __name__ == "__main__":
    unittest.main()
