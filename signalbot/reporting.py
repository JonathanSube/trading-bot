"""Baut den Status-Text fuer den /status-Telegram-Befehl des Signal-Bots -
Pendant zu tradingbot/reporting.py (ORB-Bot), aber eigenstaendig statt
geteilt: hier koennen mehrere Instrumente gleichzeitig offen sein (dict
statt einem einzelnen Feld) und der Kontostand kommt asynchron ueber die
cTrader Open API statt synchron von Alpaca.
"""

import csv
from pathlib import Path

from tradingbot.ctrader import CTraderSession, get_account_info, get_latest_price, get_open_positions
from tradingbot.setup_detection import Direction


def _money(value: float) -> str:
    """Deutsche Zahlenformatierung (1.234,56), ohne die Locale-Einstellung
    der Laufzeitumgebung zu beeinflussen."""
    sign = "+" if value > 0 else ""
    formatted = f"{value:,.2f}"  # z.B. "1,234.56"
    formatted = formatted.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{sign}{formatted}"


async def _open_position_line(session: CTraderSession, symbol: str, pos: dict, state) -> str:
    """Eine Zeile pro offener Teilposition mit aktuellem Kurs, Abstand zu
    Stop/Ziel und unrealisierter PnL - Nutzerwunsch (01.09.2026): /status
    soll den Trade-Status JETZT zeigen, nicht nur Einstiegsdaten.

    Seit 03.09.2026 koennen mehrere Teilpositionen im selben Instrument
    offen sein (siehe signalbot/state.py) - `pos` ist hier GENAU eine
    Broker-Position, ueber ihre positionId wird die zugehoerige eigene
    Teilposition (mit Stop/Ziel) herausgesucht, nicht mehr pauschal die
    erste/einzige des Symbols."""
    entry = float(pos["entryPrice"])
    qty = float(pos["volume"])
    position_id = pos.get("positionId")
    trade = next(
        (t for t in state.open_trades.get(symbol, []) if t.order_id == position_id),
        None,
    )

    base = f"  {symbol}: {qty} Lot @ {entry}"
    if trade is None:
        # Theoretisch moeglich, wenn eine Position am Broker existiert, die
        # der Bot (noch) nicht in seinem eigenen Zustand fuehrt - dann
        # fehlen Stop/Ziel, trotzdem den Kurs zeigen statt abzubrechen.
        return base

    signal = trade.signal
    direction_mult = 1 if signal.direction is Direction.LONG else -1
    try:
        current = await get_latest_price(session, symbol)
    except Exception:
        return (f"{base}\n"
                f"    Stop {signal.stop:.2f} | Ziel {signal.target:.2f} "
                f"(aktueller Kurs nicht abrufbar)")

    pnl = direction_mult * (current - entry) * qty
    to_stop = direction_mult * (signal.stop - current)
    to_target = direction_mult * (current - signal.target)
    return (f"{base}\n"
            f"    Kurs jetzt: {current:.2f} | Stop {signal.stop:.2f} (noch {abs(to_stop):.2f}) "
            f"| Ziel {signal.target:.2f} (noch {abs(to_target):.2f})\n"
            f"    Unrealisiert: {_money(pnl)}")


async def build_signal_status_report(session: CTraderSession, state, trade_log_path: Path,
                                      n_recent: int = 5) -> str:
    account = await get_account_info(session)
    lines = [f"Kontostand: {_money(account['balance'])}"]
    if state.paused:
        lines.append("Status: PAUSIERT (keine neuen Einstiege, siehe /resume)")

    open_positions = await get_open_positions(session)
    if open_positions:
        lines.append("Offene Positionen:")
        for symbol, symbol_positions in open_positions.items():
            for pos in symbol_positions:
                lines.append(await _open_position_line(session, symbol, pos, state))
    else:
        lines.append("Offene Positionen: keine")

    lines.append(f"Trades insgesamt seit Start: {state.total_trades}")
    lines.append(f"Gesamt-PnL: {_money(state.total_pnl)}")

    recent = _recent_trades(trade_log_path, n_recent)
    if recent:
        lines.append("")
        lines.append(f"Letzte {len(recent)} Trades:")
        lines.extend(recent)

    if state.stopped_permanently:
        lines.append("")
        lines.append("ACHTUNG: Bot dauerhaft gestoppt, manuelles Reset noetig.")

    return "\n".join(lines)


def _recent_trades(trade_log_path: Path, n: int) -> list[str]:
    if not trade_log_path.exists():
        return []

    with open(trade_log_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    lines = []
    for row in rows[-n:]:
        date_str = row["zeitstempel"][:10]
        pnl = float(row["pnl"])
        lines.append(
            f"{date_str} {row['symbol']} {row['richtung'].upper()} "
            f"{row['exit_grund']} {_money(pnl)}"
        )
    return lines
