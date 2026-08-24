"""Laedt QQQ 5-Min-Bars 2021-heute von Twelve Data und cached sie lokal in
data/qqq_5min_2021_2026.csv, damit nachfolgende Analysen nicht wieder
5+ Minuten auf die ratenbegrenzte API warten muessen.
"""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from tradingbot.data import load_twelvedata_bars, save_bars_csv

SYMBOL = "QQQ"
NY = ZoneInfo("America/New_York")
START = datetime(2021, 1, 1, tzinfo=NY)
END = datetime(2026, 8, 19, tzinfo=NY)
CACHE_PATH = ROOT / "data" / "qqq_5min_2021_2026.csv"


def main() -> None:
    print(f"Lade {SYMBOL} 5-Min-Bars, {START.date()} bis {END.date()} ...")
    bars = load_twelvedata_bars(SYMBOL, start=START, end=END)
    save_bars_csv(bars, CACHE_PATH)
    print(f"{len(bars)} Bars gespeichert unter {CACHE_PATH}")


if __name__ == "__main__":
    main()
