"""CSV-Protokoll ALLER vom Signal-Bot ausgewerteten Kanal-Nachrichten
(nicht nur die, die zu einem Trade fuehrten) - signal_channel_log.csv.
Nutzerwunsch (28.08.2026): die letzten sieben Tage sollen jederzeit
nachvollziehbar sein (z. B. warum ein Signal uebersprungen wurde, oder um
echte Beispieltexte fuer den Gemini-Prompt zu sammeln), ohne dafuer extra
scripts/dump_channel_history.py per GitHub-Actions-Lauf zu triggern.

Alte Zeilen (aelter als RETENTION, nach dem Kanal-eigenen Zeitstempel der
Nachricht) werden bei jedem Schreiben verworfen, damit die Datei nicht
unbegrenzt waechst - die ganze Datei wird dafuer bei jedem Aufruf neu
geschrieben, was bei den hier anfallenden Mengen (wenige hundert
Nachrichten/Woche) unproblematisch ist."""

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

RETENTION = timedelta(days=7)
COLUMNS = ["kanal_zeitstempel", "nachricht_id", "text", "geparst_json", "aktion"]


def append_channel_message(path: Path, msg_date: datetime, message_id: int, text: str,
                            parsed: dict | None, action: str) -> None:
    """action: kurzer Grund, was mit der Nachricht passiert ist
    (z. B. "trade_eroeffnet", "kein_signal", "bereits_offen",
    "index_nicht_unterstuetzt", "gemini_fehler", "stueckzahl_zu_klein",
    "order_fehlgeschlagen", "kurs_nicht_ladbar") - dient der
    Nachvollziehbarkeit, warum ein Kanal-Signal ggf. NICHT gehandelt
    wurde."""
    rows = _load_recent_rows(path, now=msg_date)
    rows.append([
        msg_date.isoformat(),
        message_id,
        text,
        json.dumps(parsed, ensure_ascii=False) if parsed is not None else "",
        action,
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(rows)


def _load_recent_rows(path: Path, now: datetime) -> list[list[str]]:
    if not path.exists():
        return []
    cutoff = now - RETENTION
    rows = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # Header ueberspringen
        for row in reader:
            if not row:
                continue
            try:
                ts = datetime.fromisoformat(row[0])
            except (ValueError, IndexError):
                continue
            if ts >= cutoff:
                rows.append(row)
    return rows
