"""Tests fuer die reinen Teile von signalbot/reporting.py.
build_signal_status_report selbst braucht die echte cTrader-Verbindung
und wird ueber scripts/run_signal_bot.py/Telegram manuell geprueft
(gleiches Muster wie tests/test_reporting.py fuer den ORB-Bot)."""

import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signalbot.reporting import _money, _open_position_line, _recent_trades
from signalbot.state import OpenSignalTrade, SignalBotState
from signalbot.trade_log import SignalTradeLogRow, append_trade
from tradingbot.orb_strategy import Signal
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


class OpenPositionLineTests(unittest.IsolatedAsyncioTestCase):
    def _state_with_trade(self, direction, stop, target, order_id="1"):
        state = SignalBotState()
        state.open_trades["NAS100"] = [OpenSignalTrade(
            signal=Signal(direction=direction, entry_price=500.0, stop=stop, target=target,
                           risk=2.0, entry_timestamp=datetime(2026, 8, 1, 10, 0)),
            order_id=order_id, qty=0.1, source_message_id=1,
        )]
        return state

    async def test_long_shows_price_distance_and_pnl(self):
        state = self._state_with_trade(Direction.LONG, stop=498.0, target=504.0)
        pos = {"entryPrice": 500.0, "volume": 0.1, "positionId": "1"}
        with patch("signalbot.reporting.get_latest_price", new=AsyncMock(return_value=502.0)):
            line = await _open_position_line(session=None, symbol="NAS100", pos=pos, state=state)
        self.assertIn("Kurs jetzt: 502.00", line)
        self.assertIn("Stop 498.00 (noch 4.00)", line)
        self.assertIn("Ziel 504.00 (noch 2.00)", line)
        self.assertIn("+0,20", line)

    async def test_missing_own_state_falls_back_to_broker_data_only(self):
        state = SignalBotState()
        pos = {"entryPrice": 500.0, "volume": 0.1, "positionId": "1"}
        line = await _open_position_line(session=None, symbol="UNTRACKED", pos=pos, state=state)
        self.assertEqual(line, "  UNTRACKED: 0.1 Lot @ 500.0")

    async def test_price_unavailable_still_shows_stop_and_target(self):
        state = self._state_with_trade(Direction.SHORT, stop=505.0, target=495.0)
        pos = {"entryPrice": 500.0, "volume": 0.1, "positionId": "1"}
        with patch("signalbot.reporting.get_latest_price", new=AsyncMock(side_effect=RuntimeError("kaputt"))):
            line = await _open_position_line(session=None, symbol="NAS100", pos=pos, state=state)
        self.assertIn("aktueller Kurs nicht abrufbar", line)
        self.assertIn("Stop 505.00", line)

    async def test_matches_correct_partial_position_by_order_id(self):
        """Seit 03.09.2026 koennen mehrere Teilpositionen im selben
        Instrument offen sein (Pyramiding) - _open_position_line muss die
        RICHTIGE anhand der positionId auswaehlen, nicht einfach die erste."""
        state = SignalBotState()
        state.open_trades["NAS100"] = [
            OpenSignalTrade(
                signal=Signal(direction=Direction.LONG, entry_price=500.0, stop=498.0, target=504.0,
                               risk=2.0, entry_timestamp=datetime(2026, 8, 1, 10, 0)),
                order_id="1", qty=0.1, source_message_id=1,
            ),
            OpenSignalTrade(
                signal=Signal(direction=Direction.LONG, entry_price=510.0, stop=505.0, target=520.0,
                               risk=5.0, entry_timestamp=datetime(2026, 8, 1, 10, 5)),
                order_id="2", qty=0.2, source_message_id=2,
            ),
        ]
        pos = {"entryPrice": 510.0, "volume": 0.2, "positionId": "2"}
        with patch("signalbot.reporting.get_latest_price", new=AsyncMock(return_value=515.0)):
            line = await _open_position_line(session=None, symbol="NAS100", pos=pos, state=state)
        self.assertIn("Stop 505.00", line)
        self.assertIn("Ziel 520.00", line)


if __name__ == "__main__":
    unittest.main()
