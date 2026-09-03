"""Tests fuer signalbot/state.py: Speichern/Laden muss verlustfrei sein,
insbesondere fuer das open_trades-dict (mehrere gleichzeitig offene
Positionen in unterschiedlichen Instrumenten, anders als beim ORB-Bot)."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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
                "QQQ": [OpenSignalTrade(make_signal(Direction.LONG), "order-1", 10, 42, entry_fill=500.1)],
                "DIA": [OpenSignalTrade(make_signal(Direction.SHORT), "order-2", 5, 43, entry_fill=None)],
            },
            last_channel_message_at=datetime(2026, 1, 2, 9, 58, tzinfo=timezone.utc),
            last_poll_at=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "signal_state.json"
            save_state(state, path)
            loaded = load_state(path)

        self.assertEqual(set(loaded.open_trades.keys()), {"QQQ", "DIA"})
        self.assertEqual(loaded.open_trades["QQQ"][0].qty, 10)
        self.assertEqual(loaded.open_trades["QQQ"][0].entry_fill, 500.1)
        self.assertIsNone(loaded.open_trades["DIA"][0].entry_fill)
        self.assertEqual(loaded.open_trades["DIA"][0].signal.direction, Direction.SHORT)
        self.assertEqual(loaded.last_message_id, 42)
        self.assertEqual(loaded.total_trades, 3)
        self.assertEqual(loaded.last_channel_message_at, datetime(2026, 1, 2, 9, 58, tzinfo=timezone.utc))
        self.assertEqual(loaded.last_poll_at, datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc))

    def test_multiple_partial_positions_same_symbol_round_trip(self):
        """Seit 03.09.2026 (Nutzerwunsch: Pyramiding-Unterstuetzung, siehe
        signalbot/state.py-Moduldocstring): open_trades[symbol] ist eine
        Liste, mehrere Teilpositionen im SELBEN Instrument muessen
        verlustfrei erhalten bleiben."""
        state = SignalBotState(open_trades={
            "NAS100": [
                OpenSignalTrade(make_signal(Direction.LONG), "order-1", 2.0, 100, entry_fill=29000.0),
                OpenSignalTrade(make_signal(Direction.LONG), "order-2", 1.5, 101, entry_fill=29050.0),
            ],
        })
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "signal_state.json"
            save_state(state, path)
            loaded = load_state(path)

        self.assertEqual(len(loaded.open_trades["NAS100"]), 2)
        order_ids = {t.order_id for t in loaded.open_trades["NAS100"]}
        self.assertEqual(order_ids, {"order-1", "order-2"})

    def test_legacy_single_object_format_is_read_as_one_element_list(self):
        """Vor 03.09.2026 war open_trades[symbol] ein einzelnes
        OpenSignalTrade-Objekt-dict statt einer Liste - eine bereits
        committete alte signal_state.json darf beim Laden nicht crashen."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "signal_state.json"
            path.write_text(json.dumps({
                "version": 1,
                "last_message_id": 1,
                "initial_equity": None,
                "total_pnl": 0.0,
                "total_trades": 0,
                "consecutive_api_errors": 0,
                "stopped_permanently": False,
                "open_trades": {
                    "NAS100": {
                        "signal": {
                            "direction": "long", "entry_price": 500.0, "stop": 497.5,
                            "target": 505.0, "risk": 2.5,
                            "entry_timestamp": "2026-01-02T10:05:00",
                        },
                        "order_id": "order-1", "qty": 10, "source_message_id": 42,
                        "entry_fill": None, "last_seen_edit_date": None,
                    },
                },
                "telegram_update_offset": None,
                "last_channel_message_at": None,
                "last_poll_at": None,
            }))
            loaded = load_state(path)

        self.assertEqual(len(loaded.open_trades["NAS100"]), 1)
        self.assertEqual(loaded.open_trades["NAS100"][0].order_id, "order-1")

    def test_telegram_peer_cache_round_trips(self):
        """Siehe signalbot/telegram_signals.py::_resolve_invite_link -
        ohne diesen Cache ruft jeder Lauf erneut Telegrams CheckChatInvite
        auf, was live (03.09.2026) zu einem FloodWait > 1500 Sekunden
        gefuehrt hat (kompletter Kanal-Abruf schlug waehrenddessen fehl)."""
        state = SignalBotState(telegram_peer_id=12345, telegram_peer_access_hash=67890)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "signal_state.json"
            save_state(state, path)
            loaded = load_state(path)

        self.assertEqual(loaded.telegram_peer_id, 12345)
        self.assertEqual(loaded.telegram_peer_access_hash, 67890)

    def test_missing_telegram_peer_cache_defaults_to_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "signal_state.json"
            path.write_text(json.dumps({
                "version": 1,
                "last_message_id": None,
                "initial_equity": None,
                "total_pnl": 0.0,
                "total_trades": 0,
                "consecutive_api_errors": 0,
                "stopped_permanently": False,
                "open_trades": {},
                "telegram_update_offset": None,
                "last_channel_message_at": None,
                "last_poll_at": None,
            }))
            loaded = load_state(path)

        self.assertIsNone(loaded.telegram_peer_id)
        self.assertIsNone(loaded.telegram_peer_access_hash)

    def test_last_seen_edit_date_round_trips(self):
        """Siehe scripts/run_signal_bot.py::_check_message_edits - ohne
        dieses Feld wuerde nach jedem Neustart jede Bearbeitung einer
        Quellnachricht erneut als "neu" gelten."""
        edit_date = datetime(2026, 1, 2, 10, 3, tzinfo=timezone.utc)
        trade = OpenSignalTrade(make_signal(), "order-1", 10, 42, last_seen_edit_date=edit_date)
        state = SignalBotState(open_trades={"QQQ": [trade]})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "signal_state.json"
            save_state(state, path)
            loaded = load_state(path)

        self.assertEqual(loaded.open_trades["QQQ"][0].last_seen_edit_date, edit_date)

    def test_missing_last_seen_edit_date_defaults_to_none(self):
        trade = OpenSignalTrade(make_signal(), "order-1", 10, 42)
        self.assertIsNone(trade.last_seen_edit_date)

    def test_legacy_naive_timestamps_are_normalized_to_utc(self):
        """Aeltere Zustandsdateien (vor dem Pyrogram-naive-datetime-Fix)
        koennen last_channel_message_at ohne UTC-Offset enthalten - siehe
        signalbot/telegram_signals.py. Ein solcher Wert darf beim Laden
        nicht offset-naiv bleiben, sonst crasht _should_poll_channel() mit
        "can't subtract offset-naive and offset-aware datetimes"."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "signal_state.json"
            path.write_text(json.dumps({
                "version": 1,
                "last_message_id": 1,
                "initial_equity": None,
                "total_pnl": 0.0,
                "total_trades": 0,
                "consecutive_api_errors": 0,
                "stopped_permanently": False,
                "open_trades": {},
                "telegram_update_offset": None,
                "last_channel_message_at": "2026-08-27T06:11:55",
                "last_poll_at": "2026-08-27T06:12:39.031210+00:00",
            }))
            loaded = load_state(path)

        self.assertEqual(
            loaded.last_channel_message_at,
            datetime(2026, 8, 27, 6, 11, 55, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(loaded.last_channel_message_at.tzinfo)
        now_utc = datetime(2026, 8, 27, 6, 30, tzinfo=timezone.utc)
        # Muss ohne TypeError subtrahierbar sein:
        (now_utc - loaded.last_channel_message_at).total_seconds()


if __name__ == "__main__":
    unittest.main()
