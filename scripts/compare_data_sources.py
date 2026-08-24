"""Vergleicht yfinance, Alpaca IEX und Twelve Data ueber ein identisches
Zeitfenster: Setup-Haeufigkeit und volle Backtest-Kennzahlen je Quelle.

Siehe trading-bot-spec.md Abschnitt 9, "Datenfeed weicht vom Backtest ab".
Nur zu Diagnosezwecken, nicht Teil des Live-Bots.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import yfinance as yf

from tradingbot.backtest import run_backtest, summarize
from tradingbot.data import load_alpaca_bars, load_twelvedata_bars
from tradingbot.setup_detection import Bar, detect_setups, simulate_entries

SYMBOL = "QQQ"
NY = ZoneInfo("America/New_York")
END = datetime(2026, 8, 19, tzinfo=NY)
START = END - timedelta(days=58)  # yfinance-Obergrenze fuer 5-Min-Intraday


def load_yfinance_bars() -> list[Bar]:
    df = yf.download(
        SYMBOL,
        start=START.strftime("%Y-%m-%d"),
        end=(END + timedelta(days=1)).strftime("%Y-%m-%d"),
        interval="5m",
        progress=False,
    )
    if df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    df = df.tz_convert(NY).between_time("09:30", "15:55")
    return [
        Bar(ts.to_pydatetime(), float(r.Open), float(r.High), float(r.Low), float(r.Close))
        for ts, r in df.iterrows()
    ]


def report(name: str, bars: list[Bar]) -> None:
    if not bars:
        print(f"{name}: keine Bars geladen")
        return

    days = len({b.timestamp.date() for b in bars})
    setups = detect_setups(bars)
    trades = simulate_entries(bars, setups)
    closed = run_backtest(bars)
    stats = summarize(closed, days)

    print(f"\n{name}")
    print(f"  Bars: {len(bars)}  Handelstage: {days}")
    print(f"  Setups: {len(setups)} ({len(setups)/days:.2f}/Tag)  "
          f"Trades ausgeloest: {len(trades)} ({len(trades)/days:.2f}/Tag)")
    if stats["trades"]:
        print(f"  Trefferquote: {stats['hit_rate']*100:.1f}%  "
              f"Ø R/Trade: {stats['avg_r']:.3f}  Gesamt R: {stats['total_r']:.2f}")


def main() -> None:
    print(f"Zeitraum: {START.date()} bis {END.date()}")
    report("yfinance", load_yfinance_bars())
    report("Alpaca IEX", load_alpaca_bars(SYMBOL, start=START, end=END))
    report("Twelve Data", load_twelvedata_bars(SYMBOL, start=START, end=END))
    print("\nSpec-Backtest zum Vergleich: 50.6% Trefferquote, +0.266 R/Trade, ~5.5 Trades/Tag")


if __name__ == "__main__":
    main()
