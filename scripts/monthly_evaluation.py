"""Auswertung nach einem Monat, siehe trading-bot-spec.md Abschnitt 5 und
Abschnitt 8, Schritt 6. Liest trades.csv (siehe tradingbot/trade_log.py)
und vergleicht gegen die Backtest-Erwartung aus Abschnitt 5.

Bekannte Luecke: "Anzahl uebersprungener/verfallener Setups und die
Gruende" laesst sich aus trades.csv nicht berechnen, das Log enthaelt nur
ausgefuehrte Trades (siehe Abschnitt 9, "Log fuer nicht ausgeloeste
Setups" - fuer ORB weniger relevant als fuer die alte Strategie, weil
Stueckzahl < 1 bei QQQ praktisch nie vorkommt, aber nicht Null).
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TRADE_LOG_PATH = Path(__file__).resolve().parent.parent / "trades.csv"

BACKTEST_REFERENCE = {
    "hit_rate": 0.511,
    "avg_r": 0.069,
    "trades_per_day": 0.98,
}


def main() -> None:
    if not TRADE_LOG_PATH.exists():
        print(f"Kein Trade-Log unter {TRADE_LOG_PATH} gefunden.")
        return

    with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("Trade-Log ist leer, noch keine abgeschlossenen Trades.")
        return

    n = len(rows)
    r_values = [float(r["pnl_in_r"]) for r in rows]
    slippage_values = [float(r["slippage"]) for r in rows]
    delay_values = [float(r["lauf_verspaetung_minuten"]) for r in rows]
    risk_values = [float(r["risiko"]) for r in rows]

    wins = sum(1 for r in r_values if r > 0)
    hit_rate = wins / n
    avg_r = sum(r_values) / n
    total_r = sum(r_values)

    avg_slippage_points = sum(slippage_values) / n
    avg_slippage_r = sum(s / risk for s, risk in zip(slippage_values, risk_values) if risk) / n
    avg_delay = sum(delay_values) / n

    sorted_r = sorted(r_values)
    median_r = sorted_r[n // 2] if n % 2 else (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2
    top3_share = (
        sum(sorted(r_values, reverse=True)[:3]) / total_r
        if total_r > 0 else float("nan")
    )

    dates = {r["zeitstempel"][:10] for r in rows}
    trades_per_day = n / len(dates) if dates else 0.0

    print(f"Auswertungszeitraum: {min(dates)} bis {max(dates)} ({len(dates)} Handelstage mit Trades)")
    print(f"Anzahl Trades: {n}")
    print(f"Trades/Tag: {trades_per_day:.2f} (Backtest: {BACKTEST_REFERENCE['trades_per_day']:.2f})")
    print(f"Trefferquote: {hit_rate*100:.1f}% (Backtest: {BACKTEST_REFERENCE['hit_rate']*100:.1f}%)")
    print(f"Ø R/Trade: {avg_r:.3f} (Backtest: {BACKTEST_REFERENCE['avg_r']:.3f})")
    print(f"Gesamt R: {total_r:.2f}")
    print()
    print(f"Ø Slippage: {avg_slippage_points:.4f} Punkte ({avg_slippage_r:.3f} R)")
    print(f"Ø Lauf-Verspaetung: {avg_delay:.2f} Minuten")
    print()
    print(f"Median-Trade: {median_r:.3f} R")
    print(f"Anteil bester 3 Trades am Gesamtgewinn: {top3_share*100:.1f}%")
    print()
    print("Nicht berechenbar aus diesem Log: Anzahl uebersprungener/verfallener")
    print("Setups (siehe Docstring oben, bekannte Luecke).")
    print()

    warnings = []
    if trades_per_day < BACKTEST_REFERENCE["trades_per_day"] * 0.5:
        warnings.append("Deutlich weniger Trades als erwartet.")
    if hit_rate < 0.40:
        warnings.append("Trefferquote unter 40% (Breakeven bei 2:1 CRV: 33.3%).")
    if avg_slippage_points > 0.1:
        warnings.append("Slippage ueber 0.1 Punkte (mehr als die Haelfte des erwarteten Vorteils).")

    if warnings:
        print("Abweichungen, die ernst zu nehmen waeren:")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("Keine der in Abschnitt 5 genannten kritischen Abweichungen erreicht.")


if __name__ == "__main__":
    main()
