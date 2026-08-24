"""Testet den Donchian-Trendfolge-Kandidaten mit derselben Train/Test-
Disziplin wie die Intraday-Kandidaten (siehe scripts/research_strategies.py,
research/FINDINGS.md).
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.daily_bars import to_daily_bars
from research.trend_following import run, summarize
from tradingbot.data import load_bars_csv

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "qqq_5min_2021_2026.csv"
TRAIN_TEST_CUTOFF = date(2025, 1, 1)


def report(name: str, daily_bars: list) -> None:
    if len(daily_bars) < 2:
        print(f"{name}: zu wenig Tage")
        return
    calendar_days = (daily_bars[-1].date - daily_bars[0].date).days
    trades = run(daily_bars)
    stats = summarize(trades, calendar_days)
    if stats["trades"] == 0:
        print(f"{name}: keine abgeschlossenen Trades")
        return
    print(f"{name}: {daily_bars[0].date} bis {daily_bars[-1].date}")
    print(f"  Trades: {stats['trades']}  ({stats['trades_per_year']:.1f}/Jahr)  "
          f"Ø Haltedauer {stats['avg_holding_days']:.1f} Tage")
    print(f"  Trefferquote: {stats['hit_rate']*100:.1f}%  Ø R/Trade: {stats['avg_r']:.3f}  "
          f"Gesamt R: {stats['total_r']:.2f}")


def main() -> None:
    bars = load_bars_csv(CACHE_PATH)
    daily = to_daily_bars(bars)

    train = [d for d in daily if d.date < TRAIN_TEST_CUTOFF]
    test = [d for d in daily if d.date >= TRAIN_TEST_CUTOFF]

    print("--- Trainingszeitraum (2021-2024) ---")
    report("Donchian-Trendfolge (20/10 Tage, 2x ATR-Stop)", train)

    print("\n--- Testzeitraum (2025-2026), einmaliger Blick ---")
    report("Donchian-Trendfolge (20/10 Tage, 2x ATR-Stop)", test)


if __name__ == "__main__":
    main()
