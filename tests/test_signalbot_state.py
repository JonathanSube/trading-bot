"""Tests fuer signalbot/state.py: Speichern/Laden muss verlustfrei sein,
insbesondere fuer das open_trades-dict (mehrere gleichzeitig offene
Positionen in unterschiedlichen Instrumenten, anders als beim ORB-Bot)."""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signalbot.state import OpenSignalTrade, SignalBotState, load_state, save_state
from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction


def make_signal(direction=Direction.LONG) -> Signal:
    return Signal(
        direction=direction, entry_price=500.0, stop=497.5, target=505.0,
        risk=2.5, entry_timestamp=datetime(2026, 1, 2, 10, 5),
    )


class RoundTripTests(unittest.TestCase):
    def test_empty_state_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "signal_state.json"
            save_state(SignalBotState(), path)
            loaded = load_state(path)
            self.assertEqual(loaded, SignalBotState())

    def test_missing_file_returns_fresh_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "does_not_exist.json"
            loaded = load_state(path)
            self.assertEqual(loaded, SignalBotState())

    def test_multiple_open_trades_round_trip(self):
        state = SignalBotState(
            last_message_id=42,
            initial_equity=100000.0,
            total_pnl=-50.0,
            total_trades=3,
            consecutive_api_errors=1,
            open_trades={
                "QQQ": OpenSignalTrade(make_signal(Direction.LONG), "order-1", 10, 42, entry_fill=500.1),
                "DIA": OpenSignalTrade(make_signal(Direction.SHORT), "order-2", 5, 43, entry_fill=None),
            },
            last_channel_message_at=datetime(2026, 1, 2, 9, 58),
            last_poll_at=datetime(2026, 1, 2, 10, 0),
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "signal_state.json"
            save_state(state, path)
            loaded = load_state(path)

        self.assertEqual(set(loaded.open_trades.keys()), {"QQQ", "DIA"})
        self.assertEqual(loaded.open_trades["QQQ"].qty, 10)
        self.assertEqual(loaded.open_trades["QQQ"].entry_fill, 500.1)
        self.assertIsNone(loaded.open_trades["DIA"].entry_fill)
        self.assertEqual(loaded.open_trades["DIA"].signal.direction, Direction.SHORT)
        self.assertEqual(loaded.last_message_id, 42)
        self.assertEqual(loaded.total_trades, 3)
        self.assertEqual(loaded.last_channel_message_at, datetime(2026, 1, 2, 9, 58))
        self.assertEqual(loaded.last_poll_at, datetime(2026, 1, 2, 10, 0))


if __name__ == "__main__":
    unittest.main()
