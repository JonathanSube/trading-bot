"""Vollstaendiger Strategie-Backtest (Entry, Stop, Ziel, Tagesende) auf
Alpaca-IEX-Daten, zum direkten Vergleich mit der Erwartungstabelle in
trading-bot-spec.md Abschnitt 5.

Antwortet auf die Frage: Funktioniert die Strategie auch auf dem Feed, auf
dem der Live-Bot tatsaechlich handeln wird (Free-Tier IEX, siehe Abschnitt
9 "Datenfeed weicht vom Backtest ab")? An der Strategie selbst (Abschnitt
1) wird dabei nichts veraendert.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from tradingbot.backtest import run_backtest, summarize
from tradingbot.data import load_alpaca_bars

SYMBOL = "QQQ"
LOOKBACK_DAYS = 365

BACKTEST_REFERENCE = {
    "hit_rate": 0.506,
    "avg_risk": 0.375,
    "avg_points": 0.100,
    "avg_r": 0.266,
    "trades_per_day": 5.5,
}


def main() -> None:
    bars = load_alpaca_bars(SYMBOL, lookback_days=LOOKBACK_DAYS)
    if not bars:
        print("Keine Bars geladen - Datenquelle/Zeitraum pruefen.")
        return

    trading_days = len({bar.timestamp.date() for bar in bars})
    closed = run_backtest(bars)
    stats = summarize(closed, trading_days)

    print(f"Symbol: {SYMBOL}, Zeitraum: letzte {LOOKBACK_DAYS} Tage (Alpaca IEX-Feed)")
    print(f"Handelstage: {trading_days}, Trades: {stats['trades']}")
    print()
    print(f"{'Kennzahl':<28}{'IEX-Backtest':>14}{'Spec-Backtest':>16}")
    print(f"{'Trefferquote':<28}{stats['hit_rate']*100:>13.1f}%{BACKTEST_REFERENCE['hit_rate']*100:>15.1f}%")
    print(f"{'Ø Risiko (Punkte)':<28}{stats['avg_risk']:>14.3f}{BACKTEST_REFERENCE['avg_risk']:>16.3f}")
    print(f"{'brutto Punkte/Trade':<28}{stats['avg_points']:>14.3f}{BACKTEST_REFERENCE['avg_points']:>16.3f}")
    print(f"{'brutto R/Trade':<28}{stats['avg_r']:>14.3f}{BACKTEST_REFERENCE['avg_r']:>16.3f}")
    print(f"{'Trades/Tag':<28}{stats['trades_per_day']:>14.2f}{BACKTEST_REFERENCE['trades_per_day']:>16.2f}")
    print()
    print(f"Gesamt brutto Punkte: {stats['total_points']:.2f}")
    print(f"Gesamt netto (0,02 Pkt Kosten): {stats['net_points_002']:.2f}")
    print(f"Gesamt netto (0,05 Pkt Kosten): {stats['net_points_005']:.2f}")
    print(f"Gesamt R: {stats['total_r']:.2f}")
    print()
    r = stats["exit_reasons"]
    print(f"Exit-Gruende: Stop {r['stop']}, Ziel {r['target']}, Tagesende {r['eod']}")


if __name__ == "__main__":
    main()
