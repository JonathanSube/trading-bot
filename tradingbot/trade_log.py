"""CSV-Protokollierung, siehe trading-bot-spec.md Abschnitt 4
("Protokollierung, der eigentliche Zweck des Projekts"). Spalten exakt wie
dort aufgezaehlt, plus deutschen Feldnamen in ASCII fuer die CSV-Kopfzeile
(kein Encoding-Aerger in Tabellenkalkulationen).

slippage und lauf_verspaetung_minuten sind laut Spec die wichtigsten
Spalten, siehe deren Definitionen unten.
"""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tradingbot.setup_detection import Direction

COLUMNS = [
    "zeitstempel", "richtung", "level", "entry_geplant", "entry_tatsaechlich",
    "slippage", "stop", "ziel", "stueckzahl", "risiko", "exit_grund",
    "exit_preis", "pnl", "pnl_in_r", "dauer_minuten", "lauf_verspaetung_minuten",
]


@dataclass
class TradeLogRow:
    timestamp: datetime
    direction: Direction
    level: float  # gebrochene Seite der Eroeffnungsspanne
    entry_planned: float  # Close der Ausbruchskerze (Erkennungszeitpunkt)
    entry_actual: float  # tatsaechlicher Fuellpreis laut Alpaca
    stop: float
    target: float
    qty: int
    risk: float  # Risiko pro Stueck in Punkten
    exit_reason: str  # "stop" | "target" | "eod" | "safety_stop"
    exit_price: float
    pnl: float
    duration_minutes: float
    run_delay_minutes: float  # Differenz geplanter/tatsaechlicher Lauf-Zeitpunkt

    @property
    def slippage(self) -> float:
        """Positiv = schlechter als geplant, negativ = besser, unabhaengig
        von der Richtung (siehe Abschnitt 4)."""
        sign = 1 if self.direction is Direction.LONG else -1
        return sign * (self.entry_actual - self.entry_planned)

    @property
    def pnl_in_r(self) -> float:
        total_risk = self.risk * self.qty
        return self.pnl / total_risk if total_risk else 0.0


def append_trade(path: Path, row: TradeLogRow) -> None:
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(COLUMNS)
        writer.writerow([
            row.timestamp.isoformat(),
            row.direction.value,
            row.level,
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
            row.run_delay_minutes,
        ])
