"""Schritt 1 (trading-bot-spec.md, Abschnitt 8): Setup-Erkennung gegen
historische Bars pruefen.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from tradingbot.data import load_alpaca_bars
from tradingbot.setup_detection import Direction, detect_setups, simulate_entries

SYMBOL = "QQQ"
LOOKBACK_DAYS = 365


def main() -> None:
    bars = load_alpaca_bars(SYMBOL, lookback_days=LOOKBACK_DAYS)
    if not bars:
        print("Keine Bars geladen - Datenquelle/Zeitraum pruefen.")
        return

    trading_days = len({bar.timestamp.date() for bar in bars})
    setups = detect_setups(bars)
    trades = simulate_entries(bars, setups)

    by_direction = Counter(t.direction for t in trades)

    print(f"Symbol: {SYMBOL}, Zeitraum: letzte {LOOKBACK_DAYS} Tage (Alpaca IEX-Feed)")
    print(f"Bars geladen: {len(bars)} ueber {trading_days} Handelstage")
    print()
    print(f"Setups erkannt:    {len(setups):4d}  ({len(setups) / trading_days:.2f}/Tag)")
    print(f"Trades ausgeloest: {len(trades):4d}  ({len(trades) / trading_days:.2f}/Tag)")
    print(f"  davon long:  {by_direction[Direction.LONG]}")
    print(f"  davon short: {by_direction[Direction.SHORT]}")
    print()
    print("Backtest-Erwartung (Abschnitt 5, 2021-2026): ~5.5 Trades/Tag")


if __name__ == "__main__":
    main()
