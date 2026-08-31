"""Baut den Status-Text fuer den /status-Telegram-Befehl des Signal-Bots -
Pendant zu tradingbot/reporting.py (ORB-Bot), aber eigenstaendig statt
geteilt: hier koennen mehrere Instrumente gleichzeitig offen sein (dict
statt einem einzelnen Feld) und der Kontostand kommt asynchron ueber die
cTrader Open API statt synchron von Alpaca.
"""

import csv
from pathlib import Path

from tradingbot.ctrader import CTraderSession, get_account_info, get_open_positions


def _money(value: float) -> str:
    """Deutsche Zahlenformatierung (1.234,56), ohne die Locale-Einstellung
    der Laufzeitumgebung zu beeinflussen."""
    sign = "+" if value > 0 else ""
    formatted = f"{value:,.2f}"  # z.B. "1,234.56"
    formatted = formatted.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{sign}{formatted}"


async def build_signal_status_report(session: CTraderSession, state, trade_log_path: Path,
                                      n_recent: int = 5) -> str:
    account = await get_account_info(session)
    lines = [f"Kontostand: {_money(account['balance'])}"]

    open_positions = await get_open_positions(session)
    if open_positions:
        lines.append("Offene Positionen:")
        for symbol, pos in open_positions.items():
            lines.append(f"  {symbol}: {pos['volume']} Lot @ {pos['entryPrice']}")
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
