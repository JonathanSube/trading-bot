"""Historische Bars von Alpaca laden (trading-bot-spec.md, Abschnitt 4).

Der Free-Tier liefert den IEX-Feed (eine einzelne Boerse), nicht den vollen
SIP-Konsolidierungstape, siehe Abschnitt 9, "Datenfeed weicht vom Backtest
ab". Das ist trotzdem die Datenquelle, auf der der Live-Bot laeuft.
"""

import csv
import os
import time as time_module
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from tradingbot.setup_detection import Bar

NY = ZoneInfo("America/New_York")


def load_alpaca_bars(
    symbol: str = "QQQ",
    lookback_days: int = 365,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Bar]:
    """Laedt 5-Minuten-Bars der regulaeren Session (09:30-15:55 ET).

    Entweder start/end explizit angeben (fuer reproduzierbare Vergleiche),
    oder lookback_days fuer die letzten N Kalendertage bis jetzt.
    """
    client = StockHistoricalDataClient(
        os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
    )

    if end is None:
        end = datetime.now(NY)
    if start is None:
        start = end - timedelta(days=lookback_days)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    df = client.get_stock_bars(request).df

    if hasattr(df.index, "get_level_values"):
        df = df.droplevel("symbol")

    df = df.tz_convert(NY)
    df = df.between_time("09:30", "15:55")

    return [
        Bar(ts.to_pydatetime(), float(row.open), float(row.high), float(row.low), float(row.close))
        for ts, row in df.iterrows()
    ]


TWELVEDATA_CHUNK_DAYS = 55  # bleibt unter dem 5000-Datenpunkte-Limit pro Anfrage


def _fetch_twelvedata_chunk(symbol: str, start: datetime, end: datetime) -> list[Bar]:
    resp = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": symbol,
            "interval": "5min",
            "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "America/New_York",
            "apikey": os.environ["TWELVEDATA_API_KEY"],
            "outputsize": 5000,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"Twelve Data Fehler: {data}")

    return [
        Bar(
            datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=NY),
            float(v["open"]),
            float(v["high"]),
            float(v["low"]),
            float(v["close"]),
        )
        for v in data.get("values", [])
    ]


def load_twelvedata_bars(
    symbol: str = "QQQ",
    lookback_days: int = 58,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Bar]:
    """Laedt 5-Minuten-Bars der regulaeren Session von Twelve Data, zum
    Vergleich mit load_alpaca_bars (siehe Abschnitt 9, "Datenfeed weicht
    vom Backtest ab"). Nur zu Diagnosezwecken, nicht Teil des Live-Bots.

    Fragt bei laengeren Zeitraeumen in mehreren Anfragen ab (Free-Tier
    begrenzt auf 5000 Datenpunkte pro Anfrage)."""
    if end is None:
        end = datetime.now(NY)
    if start is None:
        start = end - timedelta(days=lookback_days)

    bars: list[Bar] = []
    chunk_end = end
    first = True
    while chunk_end > start:
        if not first:
            time_module.sleep(8)  # Free-Tier: 8 Anfragen/Minute
        first = False
        chunk_start = max(start, chunk_end - timedelta(days=TWELVEDATA_CHUNK_DAYS))
        bars += _fetch_twelvedata_chunk(symbol, chunk_start, chunk_end)
        chunk_end = chunk_start - timedelta(seconds=1)

    unique = {b.timestamp: b for b in bars}
    ordered = sorted(unique.values(), key=lambda b: b.timestamp)
    return [b for b in ordered if time(9, 30) <= b.timestamp.time() <= time(15, 55)]


def save_bars_csv(bars: list[Bar], path: Path) -> None:
    """Cached Bars lokal, um wiederholte (langsame, ratenbegrenzte) API-
    Abfragen fuer denselben Zeitraum zu vermeiden."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close"])
        for b in bars:
            writer.writerow([b.timestamp.isoformat(), b.open, b.high, b.low, b.close])


def load_bars_csv(path: Path) -> list[Bar]:
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            Bar(
                datetime.fromisoformat(row["timestamp"]),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
            )
            for row in reader
        ]
