"""Prueft, ob tradingbot/orb_strategy.py (Live-Version, taeglich inkrementell)
und research/strategies.py::opening_range_breakout (Batch-Version, fuers
Backtesting benutzt) auf denselben Daten dieselben Signale liefern. Muss
uebereinstimmen, sonst waere das Live-Verhalten anders als das, was
validiert wurde.
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from research.strategies import opening_range_breakout
from tradingbot.data import load_alpaca_bars
from tradingbot.orb_strategy import build_signal, check_breakout, detect_opening_range
from tradingbot.setup_detection import Bar


def live_style_signals(bars: list[Bar]):
    by_day = defaultdict(list)
    for b in bars:
        by_day[b.timestamp.date()].append(b)

    signals = []
    for day in sorted(by_day):
        day_bars = sorted(by_day[day], key=lambda b: b.timestamp)
        rng = detect_opening_range(day_bars)
        if rng is None:
            continue

        for i in range(6, len(day_bars) - 1):
            direction = check_breakout(rng, day_bars[i])
            if direction is not None:
                sig = build_signal(direction, rng, day_bars[i + 1])
                if sig is not None:
                    signals.append(sig)
                break  # hoechstens ein Trade pro Tag

    return signals


def main() -> None:
    bars = load_alpaca_bars("QQQ", lookback_days=365)
    bars.sort(key=lambda b: b.timestamp)

    batch = opening_range_breakout(bars)
    live = live_style_signals(bars)

    print(f"Batch-Version (research/): {len(batch)} Signale")
    print(f"Live-Version (tradingbot/): {len(live)} Signale")

    if len(batch) != len(live):
        print("ABWEICHUNG in der Anzahl.")
        return

    mismatches = 0
    for b, l in zip(batch, live):
        same = (
            b.direction is l.direction
            and abs(b.entry_price - l.entry_price) < 1e-9
            and abs(b.stop - l.stop) < 1e-9
            and abs(b.target - l.target) < 1e-9
            and b.timestamp == l.entry_timestamp
        )
        if not same:
            mismatches += 1
            print(f"Abweichung: batch={b} live={l}")

    if mismatches == 0:
        print("Uebereinstimmung: alle Signale identisch.")
    else:
        print(f"{mismatches} abweichende Signale.")


if __name__ == "__main__":
    main()
