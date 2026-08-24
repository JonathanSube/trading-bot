"""Backtest-Kennzahlen je Kalenderjahr auf Twelve-Data-Bars (2021-heute),
um zu pruefen, ob die im Backtest ausgewiesene Edge (Abschnitt 5) gleich-
maessig ueber die Jahre verteilt ist oder ob juengere Jahre schwaecher
ausfallen. Nur Diagnose, siehe Abschnitt 9 "Datenfeed weicht vom Backtest
ab". An der Strategie (Abschnitt 1) wird nichts veraendert.
"""

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from tradingbot.backtest import run_backtest, summarize
from tradingbot.data import load_twelvedata_bars
from tradingbot.setup_detection import detect_setups, simulate_entries

SYMBOL = "QQQ"
NY = ZoneInfo("America/New_York")
START = datetime(2021, 1, 1, tzinfo=NY)
END = datetime(2026, 8, 19, tzinfo=NY)


def main() -> None:
    print(f"Lade {SYMBOL} 5-Min-Bars von Twelve Data, {START.date()} bis {END.date()} ...")
    bars = load_twelvedata_bars(SYMBOL, start=START, end=END)
    print(f"{len(bars)} Bars geladen.\n")

    by_year = defaultdict(list)
    for b in bars:
        by_year[b.timestamp.year].append(b)

    print(f"{'Jahr':<6}{'Tage':>6}{'Trades':>8}{'Trades/Tag':>12}{'Trefferq.':>11}{'Ø R/Trade':>11}{'Gesamt R':>11}")
    all_closed = []
    for year in sorted(by_year):
        year_bars = sorted(by_year[year], key=lambda b: b.timestamp)
        days = len({b.timestamp.date() for b in year_bars})
        setups = detect_setups(year_bars)
        trades = simulate_entries(year_bars, setups)
        closed = run_backtest(year_bars)
        all_closed += closed
        stats = summarize(closed, days) if closed else {"hit_rate": 0, "avg_r": 0, "total_r": 0}

        print(f"{year:<6}{days:>6}{len(trades):>8}{len(trades)/days:>12.2f}"
              f"{stats['hit_rate']*100:>10.1f}%{stats['avg_r']:>11.3f}{stats['total_r']:>11.2f}")

    total_days = len({b.timestamp.date() for b in bars})
    total_stats = summarize(all_closed, total_days)
    print(f"\nGesamt {START.date()}-{END.date()}: {len(all_closed)} Trades, "
          f"Trefferquote {total_stats['hit_rate']*100:.1f}%, Ø R/Trade {total_stats['avg_r']:.3f}, "
          f"Gesamt R {total_stats['total_r']:.2f}")
    print("Spec-Backtest zum Vergleich: 50.6% Trefferquote, +0.266 R/Trade, 3602 Trades, 2021-2026")


if __name__ == "__main__":
    main()
