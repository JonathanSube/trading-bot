"""Generische Backtest-Engine fuer die Strategiesuche in /research.

Bewusst getrennt von tradingbot/backtest.py: das dort simuliert die
eingefrorene "Wickless Candle Retest"-Strategie aus trading-bot-spec.md
Abschnitt 1 und bleibt unveraendert. Hier geht es um die Suche nach einer
neuen, robusten Strategie mit sauberer Train/Test-Trennung, um densselben
Overfitting-Fehler nicht zu wiederholen, den die Original-Strategie hatte
(siehe Abschnitt 9 der Spec).

Exit-Logik entspricht demselben Modell wie im Original-Backtest: Stop/Ziel
werden ab der Kerze NACH dem Signal geprueft, keine Uebernachtpositionen,
bei gleichzeitiger Beruehrung von Stop und Ziel in derselben Kerze zaehlt
konservativ der Stop.
"""

from dataclasses import dataclass
from datetime import date, datetime

from tradingbot.setup_detection import Bar, Direction


@dataclass(frozen=True)
class Signal:
    direction: Direction
    index: int  # Bar-Index, an dem der Einstieg erfolgt (nicht der Signal-Kerze)
    entry_price: float
    stop: float
    target: float
    timestamp: datetime


@dataclass(frozen=True)
class ClosedTrade:
    direction: Direction
    entry_price: float
    entry_timestamp: datetime
    exit_price: float
    exit_timestamp: datetime
    exit_reason: str  # "stop" | "target" | "eod"
    risk: float
    points: float
    r_multiple: float


def close_signal(bars: list[Bar], signal: Signal) -> ClosedTrade:
    risk = abs(signal.entry_price - signal.stop)
    entry_day = signal.timestamp.date()
    after = [b for b in bars[signal.index + 1:] if b.timestamp.date() == entry_day]

    def finish(exit_price: float, exit_timestamp: datetime, reason: str) -> ClosedTrade:
        points = (
            exit_price - signal.entry_price
            if signal.direction is Direction.LONG
            else signal.entry_price - exit_price
        )
        return ClosedTrade(
            signal.direction, signal.entry_price, signal.timestamp,
            exit_price, exit_timestamp, reason, risk, points, points / risk,
        )

    if not after:
        entry_bar = bars[signal.index]
        return finish(entry_bar.close, entry_bar.timestamp, "eod")

    for bar in after:
        if signal.direction is Direction.LONG:
            hit_stop, hit_target = bar.low <= signal.stop, bar.high >= signal.target
        else:
            hit_stop, hit_target = bar.high >= signal.stop, bar.low <= signal.target

        if hit_stop:
            return finish(signal.stop, bar.timestamp, "stop")
        if hit_target:
            return finish(signal.target, bar.timestamp, "target")

    last = after[-1]
    return finish(last.open, last.timestamp, "eod")


def run(bars: list[Bar], signals: list[Signal]) -> list[ClosedTrade]:
    return [close_signal(bars, s) for s in signals]


def summarize(trades: list[ClosedTrade], trading_days: int) -> dict:
    n = len(trades)
    if n == 0 or trading_days == 0:
        return {"trades": 0, "trades_per_day": 0.0, "hit_rate": 0.0, "avg_r": 0.0, "total_r": 0.0}

    wins = sum(1 for t in trades if t.r_multiple > 0)
    total_r = sum(t.r_multiple for t in trades)
    total_points = sum(t.points for t in trades)

    return {
        "trades": n,
        "trades_per_day": n / trading_days,
        "hit_rate": wins / n,
        "avg_r": total_r / n,
        "total_r": total_r,
        "avg_points": total_points / n,
        "total_points": total_points,
        "net_points_002": total_points - 0.02 * n,
    }


def split_by_date(bars: list[Bar], cutoff: date) -> tuple[list[Bar], list[Bar]]:
    """Teilt bars an cutoff: [start, cutoff) als Trainings-, [cutoff, ende]
    als Testdaten. Bar-Indizes innerhalb jeder Haelfte bleiben konsistent,
    weil beide Haelften eigenstaendige Listen sind (keine gemeinsame
    Indizierung noetig, jede Strategie arbeitet nur innerhalb einer
    Haelfte)."""
    train = [b for b in bars if b.timestamp.date() < cutoff]
    test = [b for b in bars if b.timestamp.date() >= cutoff]
    return train, test
