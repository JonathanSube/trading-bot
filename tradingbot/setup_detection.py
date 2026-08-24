"""Setup-Erkennung und Einstiegs-Simulation fuer "Wickless Candle Retest".

Reine Logik ohne Abhaengigkeit zu einem Broker oder einer Datenquelle.
Regeln exakt nach trading-bot-spec.md, Abschnitt 1 - hier nichts hinzugefuegt
und nichts veraendert.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

WICK_TOLERANCE = 0.02
EXPIRY_CANDLES = 10


class Direction(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Setup:
    direction: Direction
    level: float
    range: float
    created_index: int
    created_timestamp: datetime


@dataclass(frozen=True)
class Trade:
    direction: Direction
    level: float
    range: float
    setup_index: int
    entry_index: int
    entry_timestamp: datetime


def detect_setup(bar: Bar, index: int) -> Setup | None:
    """Prueft eine einzelne abgeschlossene Kerze auf ein Long- oder Short-Setup."""
    rng = bar.high - bar.low
    if rng <= 0:
        return None

    if bar.close > bar.open:
        lower_wick = min(bar.open, bar.close) - bar.low
        if lower_wick <= WICK_TOLERANCE * rng:
            return Setup(Direction.LONG, bar.low, rng, index, bar.timestamp)
    elif bar.close < bar.open:
        upper_wick = bar.high - max(bar.open, bar.close)
        if upper_wick <= WICK_TOLERANCE * rng:
            return Setup(Direction.SHORT, bar.high, rng, index, bar.timestamp)

    return None


def detect_setups(bars: list[Bar]) -> list[Setup]:
    """Wendet detect_setup auf jede Kerze der Serie an."""
    setups = []
    for index, bar in enumerate(bars):
        setup = detect_setup(bar, index)
        if setup is not None:
            setups.append(setup)
    return setups


def simulate_entries(bars: list[Bar], setups: list[Setup]) -> list[Trade]:
    """Prueft je Setup, ob es innerhalb von EXPIRY_CANDLES erneut beruehrt wird.

    Ein Setup loest hoechstens einen Trade aus (erste Beruehrung, danach
    entfernt), sonst verfaellt es unausgeloest.
    """
    trades = []
    last_index = len(bars) - 1

    for setup in setups:
        window_end = min(setup.created_index + EXPIRY_CANDLES, last_index)

        for index in range(setup.created_index + 1, window_end + 1):
            bar = bars[index]
            if setup.direction is Direction.LONG:
                triggered = bar.low <= setup.level
            else:
                triggered = bar.high >= setup.level

            if triggered:
                trades.append(
                    Trade(
                        setup.direction,
                        setup.level,
                        setup.range,
                        setup.created_index,
                        index,
                        bar.timestamp,
                    )
                )
                break

    return trades
