"""Aggregiert 5-Min-Bars zu Tageskerzen, fuer mehrtaegige Strategien."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from tradingbot.setup_detection import Bar


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: float
    high: float
    low: float
    close: float


def to_daily_bars(bars: list[Bar]) -> list[DailyBar]:
    by_day: dict[date, list[Bar]] = defaultdict(list)
    for b in bars:
        by_day[b.timestamp.date()].append(b)

    daily = []
    for day in sorted(by_day):
        day_bars = sorted(by_day[day], key=lambda b: b.timestamp)
        daily.append(DailyBar(
            date=day,
            open=day_bars[0].open,
            high=max(b.high for b in day_bars),
            low=min(b.low for b in day_bars),
            close=day_bars[-1].close,
        ))
    return daily
