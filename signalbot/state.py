"""Zustandsspeicherung fuer den Telegram-Signal-Bot, getrennt vom
ORB-Bot-Zustand (state.json) - eigene Datei signal_state.json, eigenes
Protokoll signal_trades.csv. Siehe trading-bot-spec.md, Feature
"Telegram-Signal-Ausfuehrung": komplett getrennter Workflow, teilt sich
mit dem ORB-Bot nur die Kill-Switch-Datei (STOP) und das Alpaca-Konto.

Im Unterschied zum ORB-Bot (hoechstens ein Trade pro Tag, ein Instrument)
kann hier gleichzeitig je eine offene Position in QQQ UND DIA bestehen
(zwei verschiedene Signalquellen-Instrumente) - deshalb open_trades als
dict statt einzelnem Feld.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

STATE_VERSION = 1


@dataclass
class OpenSignalTrade:
    signal: Signal
    order_id: str
    qty: int
    source_message_id: int
    entry_fill: float | None = None


@dataclass
class SignalBotState:
    last_message_id: int | None = None
    initial_equity: float | None = None
    total_pnl: float = 0.0
    total_trades: int = 0
    consecutive_api_errors: int = 0
    stopped_permanently: bool = False
    open_trades: dict[str, OpenSignalTrade] = field(default_factory=dict)
    telegram_update_offset: int | None = None
    # Fuer die Ruhe-Drosselung (30 Min. ohne neue Nachricht -> nur noch alle
    # 5 Min. tatsaechlich abfragen, siehe scripts/run_signal_bot.py):
    # last_channel_message_at ist Telegrams eigener Sendezeitpunkt der
    # zuletzt gesehenen Nachricht, last_poll_at der Zeitpunkt des letzten
    # tatsaechlichen Kanal-Abrufs (nicht jeder Lauf fragt wirklich ab).
    last_channel_message_at: datetime | None = None
    last_poll_at: datetime | None = None


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


def _open_trade_to_dict(trade: OpenSignalTrade) -> dict:
    return {
        "signal": _signal_to_dict(trade.signal),
        "order_id": trade.order_id,
        "qty": trade.qty,
        "source_message_id": trade.source_message_id,
        "entry_fill": trade.entry_fill,
    }


def _open_trade_from_dict(data: dict) -> OpenSignalTrade:
    return OpenSignalTrade(
        signal=_signal_from_dict(data["signal"]),
        order_id=data["order_id"],
        qty=data["qty"],
        source_message_id=data["source_message_id"],
        entry_fill=data.get("entry_fill"),
    )


def _state_to_dict(state: SignalBotState) -> dict:
    return {
        "version": STATE_VERSION,
        "last_message_id": state.last_message_id,
        "initial_equity": state.initial_equity,
        "total_pnl": state.total_pnl,
        "total_trades": state.total_trades,
        "consecutive_api_errors": state.consecutive_api_errors,
        "stopped_permanently": state.stopped_permanently,
        "open_trades": {symbol: _open_trade_to_dict(t) for symbol, t in state.open_trades.items()},
        "telegram_update_offset": state.telegram_update_offset,
        "last_channel_message_at": (
            state.last_channel_message_at.isoformat() if state.last_channel_message_at else None
        ),
        "last_poll_at": state.last_poll_at.isoformat() if state.last_poll_at else None,
    }


def _state_from_dict(data: dict) -> SignalBotState:
    return SignalBotState(
        last_message_id=data.get("last_message_id"),
        initial_equity=data.get("initial_equity"),
        total_pnl=data.get("total_pnl", 0.0),
        total_trades=data.get("total_trades", 0),
        consecutive_api_errors=data.get("consecutive_api_errors", 0),
        stopped_permanently=data.get("stopped_permanently", False),
        open_trades={
            symbol: _open_trade_from_dict(t) for symbol, t in data.get("open_trades", {}).items()
        },
        telegram_update_offset=data.get("telegram_update_offset"),
        last_channel_message_at=(
            datetime.fromisoformat(data["last_channel_message_at"])
            if data.get("last_channel_message_at")
            else None
        ),
        last_poll_at=datetime.fromisoformat(data["last_poll_at"]) if data.get("last_poll_at") else None,
    )


def load_state(path: Path) -> SignalBotState:
    if not path.exists():
        return SignalBotState()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _state_from_dict(data)


def save_state(state: SignalBotState, path: Path) -> None:
    """Atomar schreiben (temp-Datei + rename), wie state.json des
    ORB-Bots - siehe dessen Begruendung in tradingbot/state.py."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".signal_state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_state_to_dict(state), f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
