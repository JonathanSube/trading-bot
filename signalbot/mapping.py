"""Uebersetzung von Index-Signalen (NASDAQ INDEX, DOW JONES INDEX) auf
handelbare ETF-Proxys bei Alpaca. Siehe trading-bot-spec.md, Feature
"Telegram-Signal-Ausfuehrung".

Der Kanal handelt die Indizes direkt, Alpaca bietet Indizes selbst nicht
zum Handel an. QQQ (Nasdaq-100) und DIA (Dow Jones Industrial Average)
sind die liquidesten 1:1-Tracking-ETFs dafuer.

Levels aus dem Signal (falls genannt) stehen in Index-Punkten, nicht im
ETF-Kurs - eine direkte Uebernahme der Zahlen waere falsch. Stattdessen
wird der prozentuale Abstand zwischen Entry- und Stop-Level im Index
berechnet und auf den tatsaechlichen ETF-Kurs beim Einstieg angewendet.
Fehlt ein Stop-Level in der Nachricht, greift DEFAULT_STOP_PCT. Das Ziel
folgt, wie beim ORB-Bot (Abschnitt 1), der festen 2:1-CRV-Konvention,
sofern die Nachricht kein eigenes Ziel-Level nennt.
"""

from datetime import datetime

from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

INDEX_TO_SYMBOL = {
    "NASDAQ": "QQQ",
    "DOW": "DIA",
}

DEFAULT_STOP_PCT = 0.005  # 0,5 %, falls die Nachricht kein Stop-Level nennt
TARGET_R = 2.0  # gleiche Ziel-Konvention wie ORB, falls kein Ziel-Level genannt


def symbol_for_index(index_name: str | None) -> str | None:
    return INDEX_TO_SYMBOL.get(index_name) if index_name else None


def build_signal_from_parsed(parsed: dict, etf_price: float, entry_timestamp: datetime) -> Signal | None:
    """parsed: Ausgabe von signalbot.parser.parse_signal_message.
    etf_price: aktueller Kurs des zugeordneten ETF (Basis fuer Entry, da
    Market-Order - siehe Docstring oben zur Punkte/Kurs-Uebersetzung)."""
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

    entry_price = etf_price
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
