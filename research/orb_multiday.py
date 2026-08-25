"""Testvariante von Opening Range Breakout ohne Tagesende-Zwang: gleiche
Eroeffnungsspanne, gleicher Stop, gleiches 2:1-Ziel wie im Original
(research/strategies.py::opening_range_breakout), aber die Position bleibt
offen, bis Stop oder Ziel getroffen wird, auch ueber mehrere Tage hinweg,
statt spaetestens am selben Tag zwangsweise zu schliessen.

Waehrend eine Position offen ist, wird an keinem weiteren Tag (auch nicht
am Tag des Ausstiegs selbst) nach einem neuen Signal gesucht, konsistent
mit der "hoechstens ein Trade"-Regel des Originals, nur auf den gesamten
offenen Zeitraum statt auf einen Tag bezogen.
"""

from collections import defaultdict

from research.engine import ClosedTrade
from tradingbot.setup_detection import Bar, Direction

OR_BARS = 6
TARGET_R = 2.0


def _by_day(bars: list[Bar]) -> dict:
    days = defaultdict(list)
    for i, b in enumerate(bars):
        days[b.timestamp.date()].append(i)
    return days


def run(bars: list[Bar]) -> list[ClosedTrade]:
    by_day = _by_day(bars)
    days_sorted = sorted(by_day.keys())

    trades: list[ClosedTrade] = []
    day_pos = 0

    while day_pos < len(days_sorted):
        day = days_sorted[day_pos]
        idxs = by_day[day]
        if len(idxs) <= OR_BARS + 1:
            day_pos += 1
            continue

        or_bars = [bars[i] for i in idxs[:OR_BARS]]
        or_high = max(b.high for b in or_bars)
        or_low = min(b.low for b in or_bars)

        trade_opened_this_day = False
        for pos in range(OR_BARS, len(idxs) - 1):
            i = idxs[pos]
            bar = bars[i]
            entry_bar = bars[i + 1]
            if entry_bar.timestamp.date() != day:
                break

            direction = None
            if bar.close > or_high:
                direction, stop = Direction.LONG, or_low
            elif bar.close < or_low:
                direction, stop = Direction.SHORT, or_high

            if direction is None:
                continue

            entry = entry_bar.open
            risk = abs(entry - stop)
            if risk <= 0:
                break
            target = entry + TARGET_R * risk if direction is Direction.LONG else entry - TARGET_R * risk

            exit_price = exit_ts = exit_reason = None
            for j in range(i + 2, len(bars)):
                b = bars[j]
                if direction is Direction.LONG:
                    hit_stop, hit_target = b.low <= stop, b.high >= target
                else:
                    hit_stop, hit_target = b.high >= stop, b.low <= target

                if hit_stop:
                    exit_price, exit_ts, exit_reason = stop, b.timestamp, "stop"
                    break
                if hit_target:
                    exit_price, exit_ts, exit_reason = target, b.timestamp, "target"
                    break

            trade_opened_this_day = True
            if exit_price is None:
                # Daten zu Ende, Position bleibt offen -> nicht als
                # abgeschlossenen Trade werten, Auswertung stoppt hier.
                day_pos = len(days_sorted)
                break

            points = exit_price - entry if direction is Direction.LONG else entry - exit_price
            trades.append(ClosedTrade(
                direction, entry, entry_bar.timestamp, exit_price, exit_ts,
                exit_reason, risk, points, points / risk,
            ))

            exit_date = exit_ts.date()
            next_day_pos = day_pos + 1
            while next_day_pos < len(days_sorted) and days_sorted[next_day_pos] <= exit_date:
                next_day_pos += 1
            day_pos = next_day_pos
            break

        if not trade_opened_this_day:
            day_pos += 1

    return trades
