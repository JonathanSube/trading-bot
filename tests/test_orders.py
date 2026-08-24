"""Tests fuer die reine Positionsgroessen-Berechnung in tradingbot/orders.py
(Abschnitt 1, "Positionsgroesse"). Order-Platzierung selbst braucht die
echte Alpaca-API und wird laut Abschnitt 8 Schritt 4 manuell geprueft,
siehe scripts/place_test_order.py.
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.orb_strategy import Signal
from tradingbot.orders import position_size
from tradingbot.setup_detection import Direction


class PositionSizeTests(unittest.TestCase):
    def test_risk_based_size_when_affordable(self):
        # Risiko 2.0, 1% von 100.000 = 1000 -> 500 Stueck, Kaufkraft reicht locker
        signal = Signal(Direction.LONG, 100.0, 98.0, 104.0, 2.0, datetime(2026, 1, 1))
        qty = position_size(signal, equity=100_000.0, buying_power=1_000_000.0)
        self.assertEqual(qty, 500)

    def test_capped_by_buying_power(self):
        # 1% von 100.000 / Risiko 0.5 = 2000 Stueck gewuenscht, aber nur
        # Kaufkraft fuer 50.000/100 = 500 Stueck vorhanden
        signal = Signal(Direction.LONG, 100.0, 99.5, 101.0, 0.5, datetime(2026, 1, 1))
        qty = position_size(signal, equity=100_000.0, buying_power=50_000.0)
        self.assertEqual(qty, 500)

    def test_zero_risk_returns_zero(self):
        signal = Signal(Direction.LONG, 100.0, 100.0, 100.0, 0.0, datetime(2026, 1, 1))
        self.assertEqual(position_size(signal, equity=100_000.0, buying_power=1_000_000.0), 0)

    def test_rounds_down_to_whole_shares(self):
        signal = Signal(Direction.LONG, 100.0, 97.0, 106.0, 3.0, datetime(2026, 1, 1))
        # 1% von 100.000 / 3.0 = 333.33... -> 333
        qty = position_size(signal, equity=100_000.0, buying_power=1_000_000.0)
        self.assertEqual(qty, 333)


if __name__ == "__main__":
    unittest.main()
