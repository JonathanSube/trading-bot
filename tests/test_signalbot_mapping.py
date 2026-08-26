"""Tests fuer signalbot/mapping.py: Uebersetzung von Index-Signalen auf
ETF-Proxys und Stop/Ziel-Berechnung."""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signalbot.mapping import DEFAULT_STOP_PCT, TARGET_R, build_signal_from_parsed, symbol_for_index
from tradingbot.setup_detection import Direction

NOW = datetime(2026, 1, 2, 10, 0)


class SymbolForIndexTests(unittest.TestCase):
    def test_nasdaq_maps_to_qqq(self):
        self.assertEqual(symbol_for_index("NASDAQ"), "QQQ")

    def test_dow_maps_to_dia(self):
        self.assertEqual(symbol_for_index("DOW"), "DIA")

    def test_unknown_index_returns_none(self):
        self.assertIsNone(symbol_for_index("SP500"))

    def test_none_returns_none(self):
        self.assertIsNone(symbol_for_index(None))


class BuildSignalFromParsedTests(unittest.TestCase):
    def test_not_a_signal_returns_none(self):
        parsed = {"is_signal": False, "index": "NASDAQ", "direction": "long"}
        self.assertIsNone(build_signal_from_parsed(parsed, 500.0, NOW))

    def test_missing_direction_returns_none(self):
        parsed = {"is_signal": True, "index": "NASDAQ", "direction": None}
        self.assertIsNone(build_signal_from_parsed(parsed, 500.0, NOW))

    def test_unknown_index_returns_none(self):
        parsed = {"is_signal": True, "index": "SP500", "direction": "long"}
        self.assertIsNone(build_signal_from_parsed(parsed, 500.0, NOW))

    def test_long_without_levels_uses_default_stop_pct(self):
        parsed = {"is_signal": True, "index": "NASDAQ", "direction": "long",
                   "entry_level": None, "stop_level": None, "target_level": None}
        signal = build_signal_from_parsed(parsed, 500.0, NOW)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, Direction.LONG)
        self.assertEqual(signal.entry_price, 500.0)
        self.assertAlmostEqual(signal.stop, 500.0 * (1 - DEFAULT_STOP_PCT))
        self.assertAlmostEqual(signal.risk, 500.0 * DEFAULT_STOP_PCT)
        self.assertAlmostEqual(signal.target, 500.0 + TARGET_R * signal.risk)

    def test_short_with_explicit_levels_translates_percentage(self):
        # Index-Stop 150 Punkte (=1%) oberhalb des Index-Entrys -> gleicher
        # Prozentsatz auf den tatsaechlichen ETF-Kurs angewendet.
        parsed = {"is_signal": True, "index": "DOW", "direction": "short",
                   "entry_level": 15000.0, "stop_level": 15150.0, "target_level": None}
        signal = build_signal_from_parsed(parsed, 100.0, NOW)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, Direction.SHORT)
        self.assertAlmostEqual(signal.stop, 101.0)
        self.assertAlmostEqual(signal.risk, 1.0)
        self.assertAlmostEqual(signal.target, 100.0 - TARGET_R * 1.0)

    def test_zero_risk_returns_none(self):
        parsed = {"is_signal": True, "index": "NASDAQ", "direction": "long",
                   "entry_level": 15000.0, "stop_level": 15000.0}
        self.assertIsNone(build_signal_from_parsed(parsed, 500.0, NOW))


if __name__ == "__main__":
    unittest.main()
