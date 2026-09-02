"""Tests fuer die regelbasierte Schnellerkennung in signalbot/parser.py
(_fast_parse) - der eigentliche Gemini-Aufruf braucht Netzwerk und wird
nicht hier, sondern nur manuell/live geprueft. Alle Nachrichten hier sind
echte, live im Kanal beobachtete Beispiele (24.08.-02.09.2026, siehe
trading-bot-spec.md), keine erfundenen Formulierungen - Nutzerwunsch
(02.09.2026): Signale in Sekunden statt mit Gemini-Latenz/-Timeout-Risiko
erkennen."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signalbot.parser import _fast_parse


class OpenSignalTests(unittest.TestCase):
    def test_nasdaq_long_with_levels(self):
        r = _fast_parse("🇺🇸 NASDAQ INDEX\nBOUGHT LONG ⬆️️ = 100%\n\nENTRY = 29,058\n\nSTOP = 28,958")
        self.assertEqual(r, {
            "is_signal": True, "action": "open", "index": "NASDAQ", "direction": "long",
            "entry_level": 29058.0, "stop_level": 28958.0, "target_level": None,
        })

    def test_dax_short_with_levels(self):
        r = _fast_parse("🇩🇪  GERMAN DAX INDEX\nSOLD SHORT 🔻 = 100%\n\nENTRY = 25,847\n\nSTOP = 25,897")
        self.assertEqual(r["action"], "open")
        self.assertEqual(r["index"], "DAX")
        self.assertEqual(r["direction"], "short")
        self.assertEqual(r["entry_level"], 25847.0)
        self.assertEqual(r["stop_level"], 25897.0)

    def test_dow_long_with_decimal_level(self):
        r = _fast_parse("🇺🇸 DOW JONES INDEX\nBOUGHT LONG ⬆️️ = 100%\n\nENTRY = 53327.5\n\nSTOP = 53227.3")
        self.assertEqual(r["index"], "DOW")
        self.assertEqual(r["entry_level"], 53327.5)
        self.assertEqual(r["stop_level"], 53227.3)

    def test_ftse_with_empty_levels(self):
        r = _fast_parse("🇬🇧 FTSE 100 INDEX\nBOUGHT LONG ⬆️️ = 100%\n\nENTRY = \n\nSTOP =")
        self.assertEqual(r["action"], "open")
        self.assertEqual(r["index"], "FTSE")
        self.assertEqual(r["direction"], "long")
        self.assertIsNone(r["entry_level"])
        self.assertIsNone(r["stop_level"])


class CloseSignalTests(unittest.TestCase):
    def test_stopped_out_with_instrument(self):
        r = _fast_parse("STOPPED OUT OF DOW MINUS 125")
        self.assertEqual(r, {
            "is_signal": True, "action": "close", "index": "DOW", "direction": None,
            "entry_level": None, "stop_level": None, "target_level": None,
        })

    def test_close_trade_alert_pattern(self):
        r = _fast_parse("CLOSE TRADE ALERT \n\n🇩🇪 CLOSING DAX INDEX trade now")
        self.assertEqual(r["action"], "close")
        self.assertEqual(r["index"], "DAX")

    def test_closed_prefix_with_result(self):
        r = _fast_parse("CLOSED FTSE.... MINUS 8 and MINUS 5")
        self.assertEqual(r["action"], "close")
        self.assertEqual(r["index"], "FTSE")

    def test_closed_before_stop_loss_hit(self):
        r = _fast_parse("CLOSED DAX BEFORE STOP LOSS HIT.. MINUS 44")
        self.assertEqual(r["action"], "close")
        self.assertEqual(r["index"], "DAX")

    def test_trade_close_alert_with_dash(self):
        text = ("TRADE- CLOSE ALERT\nTom doesnt have access to his telegram right now.\n"
                "Tom Just closed his DAX at 25761 for ~90pt")
        r = _fast_parse(text)
        self.assertEqual(r["action"], "close")
        self.assertEqual(r["index"], "DAX")

    def test_closed_with_trailing_numbers(self):
        r = _fast_parse("CLOSED DOW 3 and 2")
        self.assertEqual(r["action"], "close")
        self.assertEqual(r["index"], "DOW")

    def test_hit_target(self):
        r = _fast_parse("NASDAQ HIT TARGET +180")
        self.assertEqual(r["action"], "close")
        self.assertEqual(r["index"], "NASDAQ")


class NoMatchFallsBackToGeminiTests(unittest.TestCase):
    """Diese Faelle MUESSEN None liefern, sonst wuerde die Schnellerkennung
    eine Stop-Anpassung faelschlich als Schliessung werten oder ein
    instrumentloses/mehrdeutiges Signal falsch zuordnen - genau die Fehler,
    die der bestehende SYSTEM_PROMPT fuer Gemini schon explizit vermeidet."""

    def test_stop_move_is_not_a_close(self):
        self.assertIsNone(_fast_parse("move sl in dow to 53551"))

    def test_stop_move_plural_is_not_a_close(self):
        self.assertIsNone(_fast_parse("both stopps to 53572.9"))

    def test_moving_stop_to_breakeven_is_not_a_close(self):
        text = "STOP LOSS ALERT \n\n🇩🇪 MOVING STOP TO BREAKEVEN in DAX INDEX now"
        self.assertIsNone(_fast_parse(text))

    def test_bare_positive_number_no_instrument(self):
        self.assertIsNone(_fast_parse("+100"))

    def test_bare_negative_number_no_instrument(self):
        self.assertIsNone(_fast_parse("-8,3"))

    def test_status_word(self):
        self.assertIsNone(_fast_parse("STATUS"))

    def test_closing_the_trade_now_without_instrument(self):
        # Braucht Kanal-Historie zur Aufloesung - das kann/soll der
        # Schnellweg nicht leisten, das bleibt Gemini vorbehalten.
        self.assertIsNone(_fast_parse("closing the trade now"))

    def test_closing_both_trades_now_without_instrument(self):
        self.assertIsNone(_fast_parse("closing both trades now"))

    def test_commentary_without_instrument(self):
        self.assertIsNone(_fast_parse("DONT BOTHER COMMENTING... I KNOW WHEN I FUCK UP\n\nLOST 20"))

    def test_commentary_mentioning_instrument_without_action(self):
        text = "I will run this DAX and if I add to it, I will post it here in Telegram...."
        self.assertIsNone(_fast_parse(text))

    def test_commentary_with_stop_word_no_instrument(self):
        text = "My gut tells me I am a good reverse indicator today.... I should stop trading now...."
        self.assertIsNone(_fast_parse(text))

    def test_ambiguous_multiple_instruments(self):
        self.assertIsNone(_fast_parse("DAX and DOW both look weak today"))


if __name__ == "__main__":
    unittest.main()
