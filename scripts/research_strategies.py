"""Strategiesuche auf den vollen 6-Jahres-Daten (2021-2026), mit sauberer
Train/Test-Trennung: Kandidaten werden nur auf 2021-2024 beurteilt, der
Testzeitraum 2025-2026 wird erst am Ende einmalig angeschaut.

Siehe research/strategies.py fuer die Kandidaten und trading-bot-spec.md
Abschnitt 9 fuer den Grund (Overfitting-Fehler der Original-Strategie, den
diese Trennung vermeiden soll).
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.engine import run, split_by_date, summarize
from research.strategies import ma_reversion, opening_range_breakout
from tradingbot.data import load_bars_csv

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "qqq_5min_2021_2026.csv"
TRAIN_TEST_CUTOFF = date(2025, 1, 1)

CANDIDATES = {
    "Opening Range Breakout": opening_range_breakout,
    "MA-Reversion": ma_reversion,
}


def evaluate(name: str, signal_fn, bars: list) -> dict:
    days = len({b.timestamp.date() for b in bars})
    signals = signal_fn(bars)
    trades = run(bars, signals)
    stats = summarize(trades, days)
    print(f"{name:<28}{stats['trades']:>8}{stats['trades_per_day']:>12.2f}"
          f"{stats['hit_rate']*100:>11.1f}%{stats['avg_r']:>11.3f}{stats['total_r']:>11.2f}")
    return stats


def main() -> None:
    if not CACHE_PATH.exists():
        print(f"Keine gecachten Daten unter {CACHE_PATH}, erst scripts/fetch_and_cache_data.py laufen lassen.")
        return

    bars = load_bars_csv(CACHE_PATH)
    train, test = split_by_date(bars, TRAIN_TEST_CUTOFF)
    train_days = len({b.timestamp.date() for b in train})
    test_days = len({b.timestamp.date() for b in test})

    print(f"Training: {train[0].timestamp.date()} bis {train[-1].timestamp.date()} ({train_days} Handelstage)")
    print(f"Test (bis zum Schluss nicht angeschaut): {test[0].timestamp.date()} bis "
          f"{test[-1].timestamp.date()} ({test_days} Handelstage)\n")

    print("--- Trainingszeitraum (2021-2024) ---")
    print(f"{'Strategie':<28}{'Trades':>8}{'Trades/Tag':>12}{'Trefferq.':>12}{'Ø R/Trade':>11}{'Gesamt R':>11}")
    for name, fn in CANDIDATES.items():
        evaluate(name, fn, train)

    print("\n--- Testzeitraum (2025-2026), einmaliger Blick, danach keine Anpassung mehr ---")
    print(f"{'Strategie':<28}{'Trades':>8}{'Trades/Tag':>12}{'Trefferq.':>12}{'Ø R/Trade':>11}{'Gesamt R':>11}")
    for name, fn in CANDIDATES.items():
        evaluate(name, fn, test)


if __name__ == "__main__":
    main()
