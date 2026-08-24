"""Tests fuer tradingbot/state.py."""

import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction
from tradingbot.state import (
    BotState,
    initialize_if_needed,
    load_state,
    record_api_error,
    record_api_success,
    record_trade_result,
    roll_to_new_day_if_needed,
    save_state,
)


class LoadStateTests(unittest.TestCase):
    def test_missing_file_returns_fresh_state(self):
        with TemporaryDirectory() as tmp:
            state = load_state(Path(tmp) / "state.json")
            self.assertIsNone(state.trading_date)
            self.assertIsNone(state.initial_equity)
            self.assertFalse(state.traded_today)
            self.assertIsNone(state.open_trade)
            self.assertFalse(state.stopped_permanently)


class RoundTripTests(unittest.TestCase):
    def test_save_then_load_preserves_all_fields(self):
        signal = Signal(Direction.LONG, 100.0, 98.0, 104.0, 2.0, datetime(2026, 8, 20, 10, 5))

        state = BotState(
            trading_date=date(2026, 8, 20),
            initial_equity=100_000.0,
            start_of_day_equity=100_250.0,
            daily_pnl=-42.5,
            total_pnl=250.0,
            last_processed_candle=datetime(2026, 8, 20, 10, 30),
            traded_today=True,
            open_trade=signal,
            halted_for_day=True,
            stopped_permanently=False,
        )
        state.counters.trades_today = 3
        state.counters.consecutive_losses = 2
        state.counters.consecutive_api_errors = 1

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(state, path)
            loaded = load_state(path)

        self.assertEqual(loaded.trading_date, state.trading_date)
        self.assertEqual(loaded.initial_equity, state.initial_equity)
        self.assertEqual(loaded.start_of_day_equity, state.start_of_day_equity)
        self.assertEqual(loaded.daily_pnl, state.daily_pnl)
        self.assertEqual(loaded.total_pnl, state.total_pnl)
        self.assertEqual(loaded.last_processed_candle, state.last_processed_candle)
        self.assertEqual(loaded.halted_for_day, state.halted_for_day)
        self.assertEqual(loaded.counters.trades_today, 3)
        self.assertEqual(loaded.counters.consecutive_losses, 2)
        self.assertEqual(loaded.counters.consecutive_api_errors, 1)

        self.assertTrue(loaded.traded_today)
        self.assertEqual(loaded.open_trade, signal)

    def test_save_creates_no_leftover_tmp_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(BotState(), path)
            leftover = list(Path(tmp).glob(".state_*.tmp"))
            self.assertEqual(leftover, [])


class InitializeIfNeededTests(unittest.TestCase):
    def test_sets_equity_only_on_first_call(self):
        state = BotState()
        initialize_if_needed(state, 100_000.0)
        self.assertEqual(state.initial_equity, 100_000.0)

        initialize_if_needed(state, 999_999.0)
        self.assertEqual(state.initial_equity, 100_000.0)


class DailyRolloverTests(unittest.TestCase):
    def test_same_day_is_a_no_op(self):
        today = date(2026, 8, 20)
        state = BotState(trading_date=today, daily_pnl=-30.0, halted_for_day=True)
        state.counters.trades_today = 4

        roll_to_new_day_if_needed(state, today, current_equity=99_000.0)

        self.assertEqual(state.daily_pnl, -30.0)
        self.assertTrue(state.halted_for_day)
        self.assertEqual(state.counters.trades_today, 4)

    def test_new_day_resets_daily_fields_but_not_permanent_ones(self):
        state = BotState(
            trading_date=date(2026, 8, 19),
            initial_equity=100_000.0,
            total_pnl=555.0,
            daily_pnl=-300.0,
            halted_for_day=True,
            stopped_permanently=True,
        )
        state.counters.trades_today = 9
        state.counters.consecutive_losses = 6
        state.counters.consecutive_api_errors = 2

        roll_to_new_day_if_needed(state, date(2026, 8, 20), current_equity=99_700.0)

        self.assertEqual(state.trading_date, date(2026, 8, 20))
        self.assertEqual(state.start_of_day_equity, 99_700.0)
        self.assertEqual(state.daily_pnl, 0.0)
        self.assertEqual(state.counters.trades_today, 0)
        self.assertEqual(state.counters.consecutive_losses, 0)
        self.assertFalse(state.halted_for_day)

        # nicht tagesgebunden, bleibt unveraendert
        self.assertEqual(state.initial_equity, 100_000.0)
        self.assertEqual(state.total_pnl, 555.0)
        self.assertTrue(state.stopped_permanently)
        self.assertEqual(state.counters.consecutive_api_errors, 2)


class RecordTradeResultTests(unittest.TestCase):
    def test_loss_increments_streak_and_pnl(self):
        state = BotState()
        record_trade_result(state, -50.0)
        self.assertEqual(state.daily_pnl, -50.0)
        self.assertEqual(state.total_pnl, -50.0)
        self.assertEqual(state.counters.trades_today, 1)
        self.assertEqual(state.counters.consecutive_losses, 1)

        record_trade_result(state, -20.0)
        self.assertEqual(state.counters.consecutive_losses, 2)
        self.assertEqual(state.daily_pnl, -70.0)

    def test_win_resets_streak(self):
        state = BotState()
        state.counters.consecutive_losses = 5

        record_trade_result(state, 30.0)

        self.assertEqual(state.counters.consecutive_losses, 0)
        self.assertEqual(state.daily_pnl, 30.0)
        self.assertEqual(state.counters.trades_today, 1)


class ApiErrorCounterTests(unittest.TestCase):
    def test_error_increments_and_success_resets(self):
        state = BotState()
        record_api_error(state)
        record_api_error(state)
        self.assertEqual(state.counters.consecutive_api_errors, 2)

        record_api_success(state)
        self.assertEqual(state.counters.consecutive_api_errors, 0)


if __name__ == "__main__":
    unittest.main()
