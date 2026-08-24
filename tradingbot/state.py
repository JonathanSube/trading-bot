"""Zustandsspeicherung zwischen Workflow-Laeufen.

Siehe trading-bot-spec.md, Abschnitt 4 ("Zustand persistent halten") und
Abschnitt 8, Schritt 2. Jeder Lauf ist ein neuer Prozess (Abschnitt 2),
deshalb muss alles, was ein folgender Lauf braucht, hier explizit
gespeichert werden - nichts darf nur im Arbeitsspeicher stehen.

Was mit den Werten passiert (Schwellenwerte pruefen, Handel stoppen) ist
Schritt 3 (Sicherheitsschalter) und bewusst nicht Teil dieses Moduls: hier
wird nur festgehalten und fortgeschrieben, nicht entschieden.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

STATE_VERSION = 2


@dataclass
class SafetyCounters:
    trades_today: int = 0
    consecutive_losses: int = 0
    consecutive_api_errors: int = 0


@dataclass
class BotState:
    trading_date: date | None = None
    initial_equity: float | None = None
    start_of_day_equity: float | None = None
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    total_trades: int = 0  # seit Bot-Start, im Unterschied zu counters.trades_today
    counters: SafetyCounters = field(default_factory=SafetyCounters)
    last_processed_candle: datetime | None = None
    # Hoechstens ein Trade pro Tag (Abschnitt 1): traded_today haelt fest,
    # ob heute schon eine Order ausgeloest wurde, open_trade das Signal
    # dazu, solange die Position noch offen ist (bis EOD-Schluss oder
    # Stop/Ziel-Fill durch Alpacas Bracket-Order). open_order_id/open_qty
    # sind noetig, um einen spaeteren Lauf die Order bei Alpaca wiederfinden
    # zu lassen (Abschluss, Fuellpreis, Grund) und den Trade zu protokollieren.
    traded_today: bool = False
    open_trade: Signal | None = None
    open_order_id: str | None = None
    open_qty: int | None = None
    # tatsaechlicher Fuellpreis der Entry-Order, sobald bekannt (kann einen
    # Lauf nach der Platzierung dauern) - Basis fuer die Slippage-Spalte im
    # Trade-Log (Abschnitt 4). None, solange noch nicht gefuellt.
    open_entry_fill: float | None = None
    halted_for_day: bool = False
    stopped_permanently: bool = False
    # Abschnitt 6: einmal taeglich, nicht bei jedem der vielen Laeufe nach
    # Handelsschluss (der Workflow ist absichtlich weiter getaktet als die
    # Session, siehe .github/workflows/trading-bot.yml).
    daily_report_sent: bool = False
    # Telegram-Updates (z. B. /status) werden per Poll abgeholt, offset
    # merkt sich, welche schon verarbeitet wurden (siehe tradingbot/notify.py).
    telegram_update_offset: int | None = None


def _signal_to_dict(signal: Signal) -> dict:
    return {
        "direction": signal.direction.value,
        "entry_price": signal.entry_price,
        "stop": signal.stop,
        "target": signal.target,
        "risk": signal.risk,
        "entry_timestamp": signal.entry_timestamp.isoformat(),
    }


def _signal_from_dict(data: dict) -> Signal:
    return Signal(
        direction=Direction(data["direction"]),
        entry_price=data["entry_price"],
        stop=data["stop"],
        target=data["target"],
        risk=data["risk"],
        entry_timestamp=datetime.fromisoformat(data["entry_timestamp"]),
    )


def _state_to_dict(state: BotState) -> dict:
    return {
        "version": STATE_VERSION,
        "trading_date": state.trading_date.isoformat() if state.trading_date else None,
        "initial_equity": state.initial_equity,
        "start_of_day_equity": state.start_of_day_equity,
        "daily_pnl": state.daily_pnl,
        "total_pnl": state.total_pnl,
        "total_trades": state.total_trades,
        "counters": {
            "trades_today": state.counters.trades_today,
            "consecutive_losses": state.counters.consecutive_losses,
            "consecutive_api_errors": state.counters.consecutive_api_errors,
        },
        "last_processed_candle": (
            state.last_processed_candle.isoformat() if state.last_processed_candle else None
        ),
        "traded_today": state.traded_today,
        "open_trade": _signal_to_dict(state.open_trade) if state.open_trade else None,
        "open_order_id": state.open_order_id,
        "open_qty": state.open_qty,
        "open_entry_fill": state.open_entry_fill,
        "halted_for_day": state.halted_for_day,
        "stopped_permanently": state.stopped_permanently,
        "daily_report_sent": state.daily_report_sent,
        "telegram_update_offset": state.telegram_update_offset,
    }


def _state_from_dict(data: dict) -> BotState:
    return BotState(
        trading_date=date.fromisoformat(data["trading_date"]) if data.get("trading_date") else None,
        initial_equity=data.get("initial_equity"),
        start_of_day_equity=data.get("start_of_day_equity"),
        daily_pnl=data.get("daily_pnl", 0.0),
        total_pnl=data.get("total_pnl", 0.0),
        total_trades=data.get("total_trades", 0),
        counters=SafetyCounters(**data.get("counters", {})),
        last_processed_candle=(
            datetime.fromisoformat(data["last_processed_candle"])
            if data.get("last_processed_candle")
            else None
        ),
        traded_today=data.get("traded_today", False),
        open_trade=_signal_from_dict(data["open_trade"]) if data.get("open_trade") else None,
        open_order_id=data.get("open_order_id"),
        open_qty=data.get("open_qty"),
        open_entry_fill=data.get("open_entry_fill"),
        halted_for_day=data.get("halted_for_day", False),
        stopped_permanently=data.get("stopped_permanently", False),
        daily_report_sent=data.get("daily_report_sent", False),
        telegram_update_offset=data.get("telegram_update_offset"),
    )


def load_state(path: Path) -> BotState:
    """Laedt den Zustand aus path, oder liefert einen frischen Zustand,
    falls die Datei noch nicht existiert (erster Lauf ueberhaupt)."""
    if not path.exists():
        return BotState()

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return _state_from_dict(data)


def save_state(state: BotState, path: Path) -> None:
    """Schreibt den Zustand atomar (temp-Datei + rename), damit ein
    abgebrochener Lauf state.json nicht halb geschrieben zuruecklaesst."""
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_state_to_dict(state), f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def initialize_if_needed(state: BotState, current_equity: float) -> None:
    """Setzt initial_equity genau einmal, beim allerersten Lauf (Basis fuer
    den Gesamtverlust-Sicherheitsschalter, Abschnitt 3)."""
    if state.initial_equity is None:
        state.initial_equity = current_equity


def roll_to_new_day_if_needed(state: BotState, today: date, current_equity: float) -> None:
    """Setzt die tagesgebundenen Zaehler zurueck, wenn today ein neuer
    Handelstag ist (sonst no-op).

    Betrifft trades_today, consecutive_losses, daily_pnl, halted_for_day und
    start_of_day_equity. total_pnl, initial_equity, stopped_permanently und
    consecutive_api_errors sind bewusst nicht tagesgebunden, siehe
    trading-bot-spec.md Abschnitt 9, "Tages-Reset der
    Sicherheitsschalter-Zaehler".
    """
    if state.trading_date == today:
        return

    state.trading_date = today
    state.start_of_day_equity = current_equity
    state.daily_pnl = 0.0
    state.counters.trades_today = 0
    state.counters.consecutive_losses = 0
    state.halted_for_day = False
    state.traded_today = False
    state.open_trade = None  # sollte durch EOD-Schluss ohnehin schon leer sein
    state.open_order_id = None
    state.open_qty = None
    state.open_entry_fill = None
    state.daily_report_sent = False


def record_trade_result(state: BotState, pnl: float) -> None:
    """Aktualisiert Tages-/Gesamt-PnL sowie Trade- und Verlustserie-Zaehler
    nach einem geschlossenen Trade. Ob daraus ein Stopp folgt, entscheidet
    Schritt 3 (Sicherheitsschalter), nicht diese Funktion."""
    state.daily_pnl += pnl
    state.total_pnl += pnl
    state.total_trades += 1
    state.counters.trades_today += 1
    state.counters.consecutive_losses = 0 if pnl >= 0 else state.counters.consecutive_losses + 1


def record_api_error(state: BotState) -> None:
    state.counters.consecutive_api_errors += 1


def record_api_success(state: BotState) -> None:
    state.counters.consecutive_api_errors = 0
