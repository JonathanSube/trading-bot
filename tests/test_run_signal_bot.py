"""Tests fuer die reinen Teile von scripts/run_signal_bot.py: die
Instrument-Handelsfenster (SESSIONS) und die Ruhe-Drosselung. Die
API-abhaengigen Funktionen (Telegram-Abruf, Order-Platzierung) brauchen
die echten externen Dienste und werden manuell geprueft, siehe
trading-bot-spec.md Abschnitt 12."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_signal_bot import (
    _is_in_session,
    _session_end_approaching,
    _should_poll_channel,
)
from signalbot.state import SignalBotState

UTC = timezone.utc


def utc(y, m, d, h, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


class IsInSessionDaxTests(unittest.TestCase):
    # 26.08.2026 (Mittwoch) liegt in der Sommerzeit (CEST, UTC+2): Xetra
    # 09:00-17:30 Europe/Berlin = 07:00-15:30 UTC, 5 Min. Vorlauf ab 06:55 UTC.
    def test_within_window_on_weekday(self):
        self.assertTrue(_is_in_session(utc(2026, 8, 26, 10, 0), "DE30_EUR"))  # Mittwoch

    def test_before_pre_session_lead(self):
        self.assertFalse(_is_in_session(utc(2026, 8, 26, 6, 54), "DE30_EUR"))

    def test_after_window(self):
        self.assertFalse(_is_in_session(utc(2026, 8, 26, 15, 31), "DE30_EUR"))

    def test_weekend_excluded_even_within_hours(self):
        self.assertFalse(_is_in_session(utc(2026, 8, 29, 10, 0), "DE30_EUR"))  # Samstag

    def test_window_boundaries_inclusive(self):
        self.assertTrue(_is_in_session(utc(2026, 8, 26, 6, 55), "DE30_EUR"))  # 5 Min. vor Handelsbeginn
        self.assertTrue(_is_in_session(utc(2026, 8, 26, 15, 30), "DE30_EUR"))  # Handelsschluss, kein Nachlauf

    def test_winter_time_shifts_boundary(self):
        # 27.01.2026 (Dienstag) liegt in der Winterzeit (CET, UTC+1):
        # Xetra 09:00-17:30 Europe/Berlin = 08:00-16:30 UTC, Vorlauf ab
        # 07:55 UTC - eine Stunde spaeter als im Sommer, DST-sicher per
        # ZoneInfo.
        self.assertFalse(_is_in_session(utc(2026, 1, 27, 7, 54), "DE30_EUR"))
        self.assertTrue(_is_in_session(utc(2026, 1, 27, 7, 55), "DE30_EUR"))
        self.assertTrue(_is_in_session(utc(2026, 1, 27, 16, 30), "DE30_EUR"))
        self.assertFalse(_is_in_session(utc(2026, 1, 27, 16, 31), "DE30_EUR"))


class IsInSessionNasdaqTests(unittest.TestCase):
    # 26.08.2026 liegt in der US-Sommerzeit (EDT, UTC-4): NYSE/NASDAQ
    # 09:30-16:00 America/New_York = 13:30-20:00 UTC, 5 Min. Vorlauf ab
    # 13:25 UTC.
    def test_before_pre_session_lead(self):
        self.assertFalse(_is_in_session(utc(2026, 8, 26, 13, 24), "NAS100_USD"))

    def test_within_pre_session_lead(self):
        self.assertTrue(_is_in_session(utc(2026, 8, 26, 13, 25), "NAS100_USD"))
        self.assertTrue(_is_in_session(utc(2026, 8, 26, 13, 29), "NAS100_USD"))

    def test_at_actual_open(self):
        self.assertTrue(_is_in_session(utc(2026, 8, 26, 13, 30), "NAS100_USD"))

    def test_after_close_no_grace_period(self):
        self.assertTrue(_is_in_session(utc(2026, 8, 26, 20, 0), "US30_USD"))
        self.assertFalse(_is_in_session(utc(2026, 8, 26, 20, 1), "US30_USD"))

    def test_weekend_excluded(self):
        self.assertFalse(_is_in_session(utc(2026, 8, 29, 13, 25), "NAS100_USD"))  # Samstag


class IsInSessionUk100Tests(unittest.TestCase):
    # 26.08.2026 liegt in der britischen Sommerzeit (BST, UTC+1): FTSE
    # 08:00-16:30 Europe/London = 07:00-15:30 UTC, 5 Min. Vorlauf ab
    # 06:55 UTC.
    def test_before_pre_session_lead(self):
        self.assertFalse(_is_in_session(utc(2026, 8, 26, 6, 54), "UK100_GBP"))

    def test_within_window(self):
        self.assertTrue(_is_in_session(utc(2026, 8, 26, 6, 55), "UK100_GBP"))
        self.assertTrue(_is_in_session(utc(2026, 8, 26, 15, 30), "UK100_GBP"))

    def test_after_window(self):
        self.assertFalse(_is_in_session(utc(2026, 8, 26, 15, 31), "UK100_GBP"))

    def test_winter_time_shifts_boundary(self):
        # 27.01.2026: UK-Winterzeit ist GMT (UTC+0, keine Verschiebung
        # gegenueber Sommerzeit-UTC-Stunde -1) - FTSE 08:00-16:30 London =
        # 08:00-16:30 UTC, Vorlauf ab 07:55 UTC.
        self.assertFalse(_is_in_session(utc(2026, 1, 27, 7, 54), "UK100_GBP"))
        self.assertTrue(_is_in_session(utc(2026, 1, 27, 7, 55), "UK100_GBP"))
        self.assertTrue(_is_in_session(utc(2026, 1, 27, 16, 30), "UK100_GBP"))
        self.assertFalse(_is_in_session(utc(2026, 1, 27, 16, 31), "UK100_GBP"))


class SessionEndApproachingTests(unittest.TestCase):
    # NAS100_USD schliesst 16:00 America/New_York = 20:00 UTC im Sommer
    # (EDT) - Zwangsschluss ab 5 Min. davor.
    def test_before_threshold(self):
        self.assertFalse(_session_end_approaching(utc(2026, 8, 26, 19, 54), "NAS100_USD"))

    def test_at_and_after_threshold(self):
        self.assertTrue(_session_end_approaching(utc(2026, 8, 26, 19, 55), "NAS100_USD"))
        self.assertTrue(_session_end_approaching(utc(2026, 8, 26, 20, 0), "NAS100_USD"))

    def test_weekend_never_approaching(self):
        self.assertFalse(_session_end_approaching(utc(2026, 8, 29, 20, 0), "NAS100_USD"))


class ShouldPollChannelTests(unittest.TestCase):
    def test_no_history_yet_always_polls(self):
        state = SignalBotState()
        self.assertTrue(_should_poll_channel(state, utc(2026, 8, 26, 10, 0)))

    def test_recent_activity_polls_every_run(self):
        state = SignalBotState(
            last_channel_message_at=utc(2026, 8, 26, 9, 50),
            last_poll_at=utc(2026, 8, 26, 9, 59),
        )
        # nur 10 Minuten seit letzter Nachricht, 1 Minute seit letztem Poll
        self.assertTrue(_should_poll_channel(state, utc(2026, 8, 26, 10, 0)))

    def test_quiet_channel_throttles_between_polls(self):
        state = SignalBotState(
            last_channel_message_at=utc(2026, 8, 26, 9, 0),  # 60 Min. still
            last_poll_at=utc(2026, 8, 26, 9, 58),  # vor 2 Minuten abgefragt
        )
        self.assertFalse(_should_poll_channel(state, utc(2026, 8, 26, 10, 0)))

    def test_quiet_channel_polls_again_after_throttle_interval(self):
        state = SignalBotState(
            last_channel_message_at=utc(2026, 8, 26, 9, 0),
            last_poll_at=utc(2026, 8, 26, 9, 54),  # vor 6 Minuten abgefragt
        )
        self.assertTrue(_should_poll_channel(state, utc(2026, 8, 26, 10, 0)))

    def test_just_under_thirty_minutes_quiet_still_counts_as_active(self):
        now = utc(2026, 8, 26, 10, 0)
        state = SignalBotState(
            last_channel_message_at=now - timedelta(minutes=29, seconds=59),
            last_poll_at=now - timedelta(minutes=1),
        )
        self.assertTrue(_should_poll_channel(state, now))

    def test_exactly_thirty_minutes_quiet_already_throttles(self):
        now = utc(2026, 8, 26, 10, 0)
        state = SignalBotState(
            last_channel_message_at=now - timedelta(minutes=30),
            last_poll_at=now - timedelta(minutes=1),
        )
        self.assertFalse(_should_poll_channel(state, now))

    def test_quiet_channel_still_polls_every_run_in_dax_active_window(self):
        # 06:55-08:00 UTC: 5 Min. Vorlauf bis 60 Min. nach DAX-Sessionstart
        # (07:00 UTC) - Ruhe-Drosselung wird hier ignoriert (Nutzerwunsch
        # 27.08.2026: "erste Stunde nach Open immer reagieren").
        state = SignalBotState(
            last_channel_message_at=utc(2026, 8, 25, 16, 0),  # Vortag, lange still
            last_poll_at=utc(2026, 8, 26, 6, 59),
        )
        self.assertTrue(_should_poll_channel(state, utc(2026, 8, 26, 6, 55)))
        self.assertTrue(_should_poll_channel(state, utc(2026, 8, 26, 7, 30)))
        self.assertTrue(_should_poll_channel(state, utc(2026, 8, 26, 8, 0)))

    def test_throttle_resumes_after_dax_active_window(self):
        # Ab 08:01 UTC (> 60 Min. nach DAX-Sessionstart) greift die normale
        # Ruhe-Drosselung wieder.
        state = SignalBotState(
            last_channel_message_at=utc(2026, 8, 26, 7, 5),  # 56 Min. still
            last_poll_at=utc(2026, 8, 26, 8, 0),  # vor 1 Minute abgefragt
        )
        self.assertFalse(_should_poll_channel(state, utc(2026, 8, 26, 8, 1)))

    def test_quiet_channel_still_polls_every_run_in_nasdaq_active_window(self):
        # 13:25-14:30 UTC: 5 Min. Vorlauf bis 60 Min. nach NASDAQ-Sessionstart
        # (13:30 UTC).
        state = SignalBotState(
            last_channel_message_at=utc(2026, 8, 25, 20, 0),  # Vortag, lange still
            last_poll_at=utc(2026, 8, 26, 13, 24),
        )
        self.assertTrue(_should_poll_channel(state, utc(2026, 8, 26, 13, 25)))
        self.assertTrue(_should_poll_channel(state, utc(2026, 8, 26, 14, 0)))
        self.assertTrue(_should_poll_channel(state, utc(2026, 8, 26, 14, 30)))


if __name__ == "__main__":
    unittest.main()
