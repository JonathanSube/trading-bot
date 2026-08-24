"""Tests fuer tradingbot/safety.py gegen die Tabelle in
trading-bot-spec.md Abschnitt 3."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.safety import check_kill_switch, check_safety_switches
from tradingbot.state import BotState


class KillSwitchTests(unittest.TestCase):
    def test_no_file_means_no_trigger(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(check_kill_switch(Path(tmp) / "STOP"))

    def test_file_present_stops_everything(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "STOP"
            path.write_text("")
            result = check_kill_switch(path)
            self.assertTrue(result.block_new_entries)
            self.assertTrue(result.close_open_positions)
            self.assertTrue(result.permanent)


class SafetySwitchTests(unittest.TestCase):
    def test_nothing_triggers_on_healthy_state(self):
        state = BotState(initial_equity=100_000.0, start_of_day_equity=100_000.0)
        self.assertIsNone(check_safety_switches(state, current_equity=100_500.0))

    def test_already_permanently_stopped_stays_stopped(self):
        state = BotState(stopped_permanently=True)
        result = check_safety_switches(state, current_equity=90_000.0)
        self.assertTrue(result.permanent)

    def test_total_loss_limit_is_permanent(self):
        state = BotState(initial_equity=100_000.0, start_of_day_equity=99_000.0)
        result = check_safety_switches(state, current_equity=84_000.0)  # -16%
        self.assertTrue(result.block_new_entries)
        self.assertTrue(result.close_open_positions)
        self.assertTrue(result.permanent)
        self.assertIn("Gesamtverlust", result.reason)

    def test_daily_loss_limit_is_not_permanent(self):
        state = BotState(initial_equity=100_000.0, start_of_day_equity=100_000.0)
        result = check_safety_switches(state, current_equity=96_500.0)  # -3.5% heute
        self.assertTrue(result.block_new_entries)
        self.assertTrue(result.close_open_positions)
        self.assertFalse(result.permanent)
        self.assertIn("Tagesverlust", result.reason)

    def test_loss_streak_closes_positions_but_not_permanent(self):
        state = BotState(initial_equity=100_000.0, start_of_day_equity=100_000.0)
        state.counters.consecutive_losses = 8
        result = check_safety_switches(state, current_equity=99_800.0)
        self.assertTrue(result.close_open_positions)
        self.assertFalse(result.permanent)
        self.assertIn("Verlusttrades", result.reason)

    def test_api_errors_close_positions_but_not_permanent(self):
        state = BotState(initial_equity=100_000.0, start_of_day_equity=100_000.0)
        state.counters.consecutive_api_errors = 5
        result = check_safety_switches(state, current_equity=100_000.0)
        self.assertTrue(result.close_open_positions)
        self.assertFalse(result.permanent)
        self.assertIn("API-Fehler", result.reason)

    def test_max_trades_per_day_blocks_entries_but_keeps_positions_open(self):
        state = BotState(initial_equity=100_000.0, start_of_day_equity=100_000.0)
        state.counters.trades_today = 15
        result = check_safety_switches(state, current_equity=100_000.0)
        self.assertTrue(result.block_new_entries)
        self.assertFalse(result.close_open_positions)
        self.assertFalse(result.permanent)

    def test_data_gap_blocks_entries_but_keeps_positions_open(self):
        state = BotState(initial_equity=100_000.0, start_of_day_equity=100_000.0)
        result = check_safety_switches(state, current_equity=100_000.0, data_gap_minutes=15.0)
        self.assertTrue(result.block_new_entries)
        self.assertFalse(result.close_open_positions)
        self.assertFalse(result.permanent)

    def test_total_loss_takes_priority_over_daily_loss(self):
        state = BotState(initial_equity=100_000.0, start_of_day_equity=90_000.0)
        result = check_safety_switches(state, current_equity=84_000.0)  # -16% total, auch Tagesverlust
        self.assertIn("Gesamtverlust", result.reason)


if __name__ == "__main__":
    unittest.main()
