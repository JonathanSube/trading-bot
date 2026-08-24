"""Mehrtaegiges Trendfolge-System (Donchian-Ausbruch, "Turtle
Trading"-Klassiker), als dritter Kandidat neben Opening Range Breakout und
MA-Reversion (siehe research/strategies.py, research/FINDINGS.md).

Parameter sind die historisch ueblichen Standardwerte (20/10 Tage,
2x ATR-Stop), nicht auf diesen Daten getunt, aus demselben Grund wie bei
den anderen Kandidaten: Parametersuche auf begrenzten Daten war der Fehler
der Original-Strategie.

Andere Mechanik als research/engine.py: Positionen laufen ueber mehrere
Tage, kein Tagesende-Zwang, kein fixes CRV-Ziel, Ausstieg entweder ueber
einen ATR-Stop oder den Gegenkanal.
"""

from dataclasses import dataclass
from datetime import date
from enum import Enum

from research.daily_bars import DailyBar

ENTRY_CHANNEL = 20
EXIT_CHANNEL = 10
ATR_WINDOW = 20
STOP_ATR_MULT = 2.0


class Direction(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class ClosedTrade:
    direction: Direction
    entry_price: float
    entry_date: date
    exit_price: float
    exit_date: date
    exit_reason: str  # "stop" | "channel"
    risk: float
    points: float
    r_multiple: float
    holding_days: int


def _true_range(bars: list[DailyBar], i: int) -> float:
    if i == 0:
        return bars[i].high - bars[i].low
    prev_close = bars[i - 1].close
    return max(
        bars[i].high - bars[i].low,
        abs(bars[i].high - prev_close),
        abs(bars[i].low - prev_close),
    )


def _atr(bars: list[DailyBar], i: int, window: int) -> float:
    trs = [_true_range(bars, j) for j in range(i - window + 1, i + 1)]
    return sum(trs) / window


def run(bars: list[DailyBar]) -> list[ClosedTrade]:
    trades: list[ClosedTrade] = []
    start = max(ENTRY_CHANNEL, ATR_WINDOW)

    i = start
    while i < len(bars) - 1:
        entry_window = bars[i - ENTRY_CHANNEL:i]  # ohne Tag i selbst, kein Blick voraus
        channel_high = max(b.high for b in entry_window)
        channel_low = min(b.low for b in entry_window)

        direction = None
        if bars[i].close > channel_high:
            direction = Direction.LONG
        elif bars[i].close < channel_low:
            direction = Direction.SHORT

        if direction is None:
            i += 1
            continue

        entry_bar = bars[i + 1]
        entry_price = entry_bar.open
        atr = _atr(bars, i, ATR_WINDOW)
        if atr <= 0:
            i += 1
            continue

        stop = entry_price - STOP_ATR_MULT * atr if direction is Direction.LONG else entry_price + STOP_ATR_MULT * atr
        risk = abs(entry_price - stop)

        j = i + 1
        exit_price = exit_date = exit_reason = None
        while j < len(bars):
            day = bars[j]

            hit_stop = day.low <= stop if direction is Direction.LONG else day.high >= stop
            if hit_stop:
                exit_price, exit_date, exit_reason = stop, day.date, "stop"
                break

            if j >= i + 1:
                exit_window = bars[max(0, j - EXIT_CHANNEL):j]
                if exit_window:
                    if direction is Direction.LONG and day.close <= min(b.low for b in exit_window):
                        if j + 1 < len(bars):
                            exit_price, exit_date, exit_reason = bars[j + 1].open, bars[j + 1].date, "channel"
                        break
                    if direction is Direction.SHORT and day.close >= max(b.high for b in exit_window):
                        if j + 1 < len(bars):
                            exit_price, exit_date, exit_reason = bars[j + 1].open, bars[j + 1].date, "channel"
                        break
            j += 1

        if exit_price is None:
            break  # Position bis Datenende offen, nicht als abgeschlossenen Trade werten

        points = exit_price - entry_price if direction is Direction.LONG else entry_price - exit_price
        trades.append(ClosedTrade(
            direction, entry_price, entry_bar.date, exit_price, exit_date, exit_reason,
            risk, points, points / risk, (exit_date - entry_bar.date).days,
        ))

        i = j + 1  # naechste Suche erst nach Ende dieses Trades (keine ueberlappenden Positionen)

    return trades


def summarize(trades: list[ClosedTrade], calendar_days: int) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}

    wins = sum(1 for t in trades if t.r_multiple > 0)
    total_r = sum(t.r_multiple for t in trades)
    avg_holding = sum(t.holding_days for t in trades) / n

    return {
        "trades": n,
        "trades_per_year": n / (calendar_days / 365.25),
        "hit_rate": wins / n,
        "avg_r": total_r / n,
        "total_r": total_r,
        "avg_holding_days": avg_holding,
    }
