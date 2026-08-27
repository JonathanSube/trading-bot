"""Tests fuer die reinen Teile von scripts/run_signal_bot.py: das
EU-Handelsfenster und die Ruhe-Drosselung. Die API-abhaengigen Funktionen
(Telegram-Abruf, Order-Platzierung) brauchen die echten externen Dienste
und werden manuell geprueft, siehe trading-bot-spec.md Abschnitt 12."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_signal_bot import _is_eu_hours, _is_us_pre_session, _should_poll_channel
from signalbot.state import SignalBotState

UTC = timezone.utc


def utc(y, m, d, h, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


class IsEuHoursTests(unittest.TestCase):
    # 26.08.2026 (Mittwoch) liegt in der Sommerzeit (CEST, UTC+2): Xetra-
    # Handelsbeginn 09:00 Europe/Berlin = 07:00 UTC, 5 Min. Vorlauf ab
    # 06:55 UTC.
    def test_within_window_on_weekday(self):
        self.assertTrue(_is_eu_hours(utc(2026, 8, 26, 10, 0)))  # Mittwoch

    def test_before_pre_session_lead(self):
        self.assertFalse(_is_eu_hours(utc(2026, 8, 26, 6, 54)))

    def test_after_window(self):
        self.assertFalse(_is_eu_hours(utc(2026, 8, 26, 17, 1)))

    def test_weekend_excluded_even_within_hours(self):
        self.assertFalse(_is_eu_hours(utc(2026, 8, 29, 10, 0)))  # Samstag

    def test_window_boundaries_inclusive(self):
        self.assertTrue(_is_eu_hours(utc(2026, 8, 26, 6, 55)))  # 5 Min. vor Handelsbeginn
        self.assertTrue(_is_eu_hours(utc(2026, 8, 26, 17, 0)))

    def test_winter_time_shifts_boundary(self):
        # 27.01.2026 (Dienstag) liegt in der Winterzeit (CET, UTC+1):
        # Handelsbeginn 09:00 Europe/Berlin = 08:00 UTC, Vorlauf ab 07:55 UTC -
        # eine Stunde spaeter als im Sommer, DST-sicher per ZoneInfo.
        self.assertFalse(_is_eu_hours(utc(2026, 1, 27, 7, 54)))
        self.assertTrue(_is_eu_hours(utc(2026, 1, 27, 7, 55)))


class IsUsPreSessionTests(unittest.TestCase):
    # 26.08.2026 liegt in der US-Sommerzeit (EDT, UTC-4): NYSE/NASDAQ-
    # Handelsbeginn 09:30 America/New_York = 13:30 UTC, 5 Min. Vorlauf ab
    # 13:25 UTC.
    def test_before_pre_session_lead(self):
        self.assertFalse(_is_us_pre_session(utc(2026, 8, 26, 13, 24)))

    def test_within_pre_session_lead(self):
        self.assertTrue(_is_us_pre_session(utc(2026, 8, 26, 13, 25)))
        self.assertTrue(_is_us_pre_session(utc(2026, 8, 26, 13, 29)))

    def test_at_actual_open_is_false(self):
        # Ab dem tatsaechlichen Handelsbeginn uebernimmt
        # _is_us_market_open() (Alpaca-Clock), nicht mehr der Vorlauf.
        self.assertFalse(_is_us_pre_session(utc(2026, 8, 26, 13, 30)))

    def test_weekend_excluded(self):
        self.assertFalse(_is_us_pre_session(utc(2026, 8, 29, 13, 25)))  # Samstag


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


if __name__ == "__main__":
    unittest.main()
