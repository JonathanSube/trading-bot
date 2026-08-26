"""CSV-Protokoll fuer den Signal-Bot (signal_trades.csv), getrennt vom
ORB-Protokoll (trades.csv). Gleiche Spaltenlogik wie tradingbot/trade_log.py,
zusaetzlich Quelle (Instrument, Kanal-Nachricht-ID) fuer Nachvollziehbarkeit."""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tradingbot.setup_detection import Direction

COLUMNS = [
    "zeitstempel", "symbol", "richtung", "quelle_nachricht_id",
    "entry_geplant", "entry_tatsaechlich", "slippage", "stop", "ziel",
    "stueckzahl", "risiko", "exit_grund", "exit_preis", "pnl", "pnl_in_r",
    "dauer_minuten",
]


@dataclass
class SignalTradeLogRow:
    timestamp: datetime
    symbol: str
    direction: Direction
    source_message_id: int
    entry_planned: float
    entry_actual: float
    stop: float
    target: float
    qty: int
    risk: float
    exit_reason: str
    exit_price: float
    pnl: float
    duration_minutes: float

    @property
    def slippage(self) -> float:
        sign = 1 if self.direction is Direction.LONG else -1
        return sign * (self.entry_actual - self.entry_planned)

    @property
    def pnl_in_r(self) -> float:
        total_risk = self.risk * self.qty
        return self.pnl / total_risk if total_risk else 0.0


def append_trade(path: Path, row: SignalTradeLogRow) -> None:
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(COLUMNS)
        writer.writerow([
            row.timestamp.isoformat(),
            row.symbol,
            row.direction.value,
            row.source_message_id,
            row.entry_planned,
            row.entry_actual,
            row.slippage,
            row.stop,
            row.target,
            row.qty,
            row.risk,
            row.exit_reason,
            row.exit_price,
            row.pnl,
            row.pnl_in_r,
            row.duration_minutes,
        ])
