"""Uebersetzung von Index-Signalen (NASDAQ INDEX, DOW JONES INDEX, GERMAN
DAX INDEX, FTSE 100 INDEX) auf handelbare OANDA-CFD-Instrumente. Siehe
trading-bot-spec.md, Aenderungsprotokoll: Umstieg von Alpaca/QQQ-DIA-
ETF-Proxys auf OANDA, weil der Nutzer Kurse will, die den im Kanal
genannten Index-Punkten entsprechen - das geht nur ueber CFDs auf echten
Indizes, nicht ueber ETFs.

Levels aus dem Signal (falls genannt) stehen in den Index-Punkten des
Kanals, nicht zwingend identisch mit OANDAs eigener Quotierung desselben
Index (unterschiedliche CFD-Anbieter berechnen ihre Indexstaende leicht
abweichend) - eine direkte Uebernahme der Zahlen waere deshalb falsch.
Stattdessen wird der prozentuale Abstand zwischen Entry- und Stop-Level im
Kanal-Index berechnet und auf den tatsaechlichen OANDA-Kurs beim Einstieg
angewendet. Fehlt ein Stop-Level in der Nachricht, greift
DEFAULT_STOP_PCT. Das Ziel folgt, wie beim ORB-Bot (Abschnitt 1), der
festen 2:1-CRV-Konvention, sofern die Nachricht kein eigenes Ziel-Level
nennt.
"""

from datetime import datetime

from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

INDEX_TO_SYMBOL = {
    "NASDAQ": "NAS100_USD",
    "DOW": "US30_USD",
    "FTSE": "UK100_GBP",
    "DAX": "DE30_EUR",  # VOR GO-LIVE gegen GET /v3/accounts/{id}/instruments pruefen
}

DEFAULT_STOP_PCT = 0.005  # 0,5 %, falls die Nachricht kein Stop-Level nennt
TARGET_R = 2.0  # gleiche Ziel-Konvention wie ORB, falls kein Ziel-Level genannt


def symbol_for_index(index_name: str | None) -> str | None:
    return INDEX_TO_SYMBOL.get(index_name) if index_name else None


def build_signal_from_parsed(parsed: dict, instrument_price: float, entry_timestamp: datetime) -> Signal | None:
    """parsed: Ausgabe von signalbot.parser.parse_signal_message.
    instrument_price: aktueller OANDA-Kurs des zugeordneten Instruments
    (Basis fuer Entry, da Market-Order - siehe Docstring oben zur
    Punkte/Kurs-Uebersetzung)."""
    if not parsed.get("is_signal"):
        return None

    symbol = symbol_for_index(parsed.get("index"))
    if symbol is None:
        return None

    direction_str = parsed.get("direction")
    if direction_str not in ("long", "short"):
        return None
    direction = Direction.LONG if direction_str == "long" else Direction.SHORT

    entry_level = parsed.get("entry_level")
    stop_level = parsed.get("stop_level")
    if entry_level and stop_level and entry_level > 0:
        stop_pct = abs(entry_level - stop_level) / entry_level
    else:
        stop_pct = DEFAULT_STOP_PCT

    entry_price = instrument_price
    if direction is Direction.LONG:
        stop = entry_price * (1 - stop_pct)
        risk = entry_price - stop
        target = entry_price + TARGET_R * risk
    else:
        stop = entry_price * (1 + stop_pct)
        risk = stop - entry_price
        target = entry_price - TARGET_R * risk

    if risk <= 0:
        return None

    return Signal(direction, entry_price, stop, target, risk, entry_timestamp)
