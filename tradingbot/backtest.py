"""Vollstaendige Trade-Simulation (Entry, Stop, Ziel, Tagesende) fuer
"Wickless Candle Retest", exakt nach trading-bot-spec.md Abschnitt 1.

Baut auf tradingbot.setup_detection auf (Setup-Erkennung, Entry-Trigger)
und ergaenzt die Exit-Logik, um Kennzahlen wie im Backtest aus Abschnitt 5
zu berechnen. An der Strategie selbst wird hier nichts veraendert, nur
simuliert.

Zwei Modellierungsannahmen, die die Spec offen laesst, weil keine
Tick-Daten verfuegbar sind: Stop und Ziel werden erst ab der Kerze NACH der
Einstiegskerze geprueft, und wenn eine Kerze innerhalb desselben Tages
beides beruehrt, zaehlt konservativ der Stop.
"""

from dataclasses import dataclass
from datetime import datetime

from tradingbot.setup_detection import Bar, Direction, Trade, detect_setups, simulate_entries

STOP_RANGE_MULT = 0.5
TARGET_RISK_MULT = 1.5


@dataclass(frozen=True)
class ClosedTrade:
    direction: Direction
    entry_price: float
    entry_timestamp: datetime
    stop: float
    target: float
    risk: float
    exit_price: float
    exit_timestamp: datetime
    exit_reason: str  # "stop" | "target" | "eod"
    points: float
    r_multiple: float


def _stop_and_target(trade: Trade) -> tuple[float, float, float]:
    stop_distance = STOP_RANGE_MULT * trade.range
    if trade.direction is Direction.LONG:
        stop = trade.level - stop_distance
    else:
        stop = trade.level + stop_distance

    risk = abs(trade.level - stop)
    target = (
        trade.level + TARGET_RISK_MULT * risk
        if trade.direction is Direction.LONG
        else trade.level - TARGET_RISK_MULT * risk
    )
    return stop, target, risk


def _finish(trade: Trade, stop: float, target: float, risk: float,
            exit_price: float, exit_timestamp: datetime, reason: str) -> ClosedTrade:
    if trade.direction is Direction.LONG:
        points = exit_price - trade.level
    else:
        points = trade.level - exit_price

    return ClosedTrade(
        direction=trade.direction,
        entry_price=trade.level,
        entry_timestamp=trade.entry_timestamp,
        stop=stop,
        target=target,
        risk=risk,
        exit_price=exit_price,
        exit_timestamp=exit_timestamp,
        exit_reason=reason,
        points=points,
        r_multiple=points / risk,
    )


def close_trade(trade: Trade, bars_same_day_after_entry: list[Bar]) -> ClosedTrade:
    """Sucht ab der ersten Kerze nach dem Einstieg, was zuerst beruehrt
    wird. bars_same_day_after_entry darf keine Kerzen eines Folgetags
    enthalten (keine Uebernachtpositionen, Abschnitt 1)."""
    stop, target, risk = _stop_and_target(trade)

    for bar in bars_same_day_after_entry:
        if trade.direction is Direction.LONG:
            hit_stop = bar.low <= stop
            hit_target = bar.high >= target
        else:
            hit_stop = bar.high >= stop
            hit_target = bar.low <= target

        if hit_stop:
            return _finish(trade, stop, target, risk, stop, bar.timestamp, "stop")
        if hit_target:
            return _finish(trade, stop, target, risk, target, bar.timestamp, "target")

    # Weder Stop noch Ziel bis Handelsschluss beruehrt -> Tagesende-Zwangsschluss.
    # Naeherung fuer den Market-Order-Kurs um 15:55 ET: Open der letzten Kerze.
    last_bar = bars_same_day_after_entry[-1]
    return _finish(trade, stop, target, risk, last_bar.open, last_bar.timestamp, "eod")


def run_backtest(bars: list[Bar]) -> list[ClosedTrade]:
    setups = detect_setups(bars)
    entries = simulate_entries(bars, setups)

    closed = []
    for trade in entries:
        entry_day = trade.entry_timestamp.date()
        after = [b for b in bars[trade.entry_index + 1:] if b.timestamp.date() == entry_day]

        if not after:
            # Einstieg war schon die letzte Kerze des Tages -> sofortiger Zwangsschluss
            stop, target, risk = _stop_and_target(trade)
            entry_bar = bars[trade.entry_index]
            closed.append(_finish(trade, stop, target, risk, entry_bar.close, entry_bar.timestamp, "eod"))
            continue

        closed.append(close_trade(trade, after))

    return closed


def summarize(closed_trades: list[ClosedTrade], trading_days: int) -> dict:
    n = len(closed_trades)
    if n == 0:
        return {"trades": 0}

    wins = sum(1 for t in closed_trades if t.r_multiple > 0)
    total_points = sum(t.points for t in closed_trades)
    total_r = sum(t.r_multiple for t in closed_trades)
    reasons = {"stop": 0, "target": 0, "eod": 0}
    for t in closed_trades:
        reasons[t.exit_reason] += 1

    return {
        "trades": n,
        "trades_per_day": n / trading_days,
        "hit_rate": wins / n,
        "avg_risk": sum(t.risk for t in closed_trades) / n,
        "avg_points": total_points / n,
        "avg_r": total_r / n,
        "total_points": total_points,
        "total_r": total_r,
        "net_points_002": total_points - 0.02 * n,
        "net_points_005": total_points - 0.05 * n,
        "exit_reasons": reasons,
    }
