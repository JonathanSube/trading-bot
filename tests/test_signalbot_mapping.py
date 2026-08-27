"""Tests fuer signalbot/mapping.py: Uebersetzung von Index-Signalen auf
IG-CFD-Instrumente (Epics) und Stop/Ziel-Berechnung."""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signalbot.mapping import DEFAULT_STOP_PCT, TARGET_R, build_signal_from_parsed, symbol_for_index
from tradingbot.setup_detection import Direction

NOW = datetime(2026, 1, 2, 10, 0)


class SymbolForIndexTests(unittest.TestCase):
    def test_nasdaq_maps_to_an_epic(self):
        self.assertEqual(symbol_for_index("NASDAQ"), "IX.D.NASDAQ.IFD.IP")

    def test_dow_maps_to_an_epic(self):
        self.assertEqual(symbol_for_index("DOW"), "IX.D.DOW.IFD.IP")

    def test_ftse_maps_to_an_epic(self):
        self.assertEqual(symbol_for_index("FTSE"), "IX.D.FTSE.IFD.IP")

    def test_dax_maps_to_an_epic(self):
        self.assertEqual(symbol_for_index("DAX"), "IX.D.DAX.IFD.IP")

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
        # Prozentsatz auf den tatsaechlichen IG-Kurs angewendet.
        parsed = {"is_signal": True, "index": "DOW", "direction": "short",
                   "entry_level": 15000.0, "stop_level": 15150.0, "target_level": None}
        signal = build_signal_from_parsed(parsed, 100.0, NOW)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, Direction.SHORT)
        self.assertAlmostEqual(signal.stop, 101.0)
        self.assertAlmostEqual(signal.risk, 1.0)
        self.assertAlmostEqual(signal.target, 100.0 - TARGET_R * 1.0)

    def test_ftse_long_with_real_channel_levels(self):
        # Echte Kanal-Nachricht (27.08.2026): "FTSE 100 INDEX / BOUGHT LONG
        # / ENTRY = 10826.9 / STOP = 10821.9" -> Stop-Distanz ~0,046 %.
        parsed = {"is_signal": True, "index": "FTSE", "direction": "long",
                   "entry_level": 10826.9, "stop_level": 10821.9, "target_level": None}
        signal = build_signal_from_parsed(parsed, 8000.0, NOW)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, Direction.LONG)
        stop_pct = (10826.9 - 10821.9) / 10826.9
        self.assertAlmostEqual(signal.stop, 8000.0 * (1 - stop_pct))

    def test_dax_short_with_real_channel_levels(self):
        # Echte Kanal-Nachricht (27.08.2026): "GERMAN DAX INDEX / SOLD
        # SHORT / ENTRY = 26334.8 / STOP = 26380" -> Stop-Distanz ~0,172 %.
        parsed = {"is_signal": True, "index": "DAX", "direction": "short",
                   "entry_level": 26334.8, "stop_level": 26380.0, "target_level": None}
        signal = build_signal_from_parsed(parsed, 19000.0, NOW)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, Direction.SHORT)
        stop_pct = (26380.0 - 26334.8) / 26334.8
        self.assertAlmostEqual(signal.stop, 19000.0 * (1 + stop_pct))

    def test_zero_risk_returns_none(self):
        parsed = {"is_signal": True, "index": "NASDAQ", "direction": "long",
                   "entry_level": 15000.0, "stop_level": 15000.0}
        self.assertIsNone(build_signal_from_parsed(parsed, 500.0, NOW))


if __name__ == "__main__":
    unittest.main()
