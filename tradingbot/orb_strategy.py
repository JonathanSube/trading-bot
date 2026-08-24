"""Opening Range Breakout: aktuelle Strategie, siehe trading-bot-spec.md
Abschnitt 1. Ersetzt die urspruengliche "Wickless Candle Retest"-Strategie
in tradingbot/setup_detection.py (bleibt als historischer Code stehen,
siehe Abschnitt 9 der Spec, wird vom Live-Bot nicht mehr genutzt).
"""

from dataclasses import dataclass
from datetime import date, datetime

from tradingbot.setup_detection import Bar, Direction

OR_BARS = 6  # erste 30 Minuten (09:30-10:00)
TARGET_R = 2.0


@dataclass(frozen=True)
class OpeningRange:
    day: date
    high: float
    low: float


@dataclass(frozen=True)
class Signal:
    direction: Direction
    entry_price: float
    stop: float
    target: float
    risk: float
    entry_timestamp: datetime


def detect_opening_range(bars_today: list[Bar]) -> OpeningRange | None:
    """bars_today: alle bisher abgeschlossenen Kerzen des laufenden Tages,
    chronologisch. Liefert None, solange weniger als OR_BARS Kerzen vorliegen
    (die Eroeffnungsspanne steht erst nach 10:00 Uhr fest)."""
    if len(bars_today) < OR_BARS:
        return None

    first = bars_today[:OR_BARS]
    return OpeningRange(
        day=first[0].timestamp.date(),
        high=max(b.high for b in first),
        low=min(b.low for b in first),
    )


def check_breakout(opening_range: OpeningRange, latest_bar: Bar) -> Direction | None:
    """Prueft, ob latest_bar (die zuletzt abgeschlossene Kerze, nicht Teil
    der Eroeffnungsspanne selbst) einen Ausbruch zeigt. Ruft der Bot fuer
    jede Kerze ab der 7. des Tages auf, bis entweder ein Ausbruch gefunden
    wird oder der Tag vorbei ist (siehe "hoechstens ein Trade pro Tag" in
    der Spec: nach dem ersten Treffer nicht mehr aufrufen)."""
    if latest_bar.close > opening_range.high:
        return Direction.LONG
    if latest_bar.close < opening_range.low:
        return Direction.SHORT
    return None


def build_signal(direction: Direction, opening_range: OpeningRange, entry_bar: Bar) -> Signal | None:
    """entry_bar: die Kerze, an deren Eroeffnung eingestiegen wird (die
    Kerze NACH der Ausbruchskerze, siehe Spec Abschnitt 1 "Einstieg")."""
    entry_price = entry_bar.open

    if direction is Direction.LONG:
        stop = opening_range.low
        risk = entry_price - stop
        target = entry_price + TARGET_R * risk
    else:
        stop = opening_range.high
        risk = stop - entry_price
        target = entry_price - TARGET_R * risk

    if risk <= 0:
        return None

    return Signal(direction, entry_price, stop, target, risk, entry_bar.timestamp)
