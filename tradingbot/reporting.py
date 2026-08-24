"""Baut den Status-Text fuer den taeglichen Bericht (Abschnitt 6) und den
/status-Telegram-Befehl - beide zeigen dasselbe, nur zu unterschiedlichen
Zeitpunkten ausgeloest (einmal taeglich automatisch, einmal auf Zuruf).
"""

import csv
from datetime import date
from pathlib import Path

from alpaca.trading.client import TradingClient

from tradingbot.state import BotState


def _money(value: float) -> str:
    """Deutsche Zahlenformatierung (1.234,56), ohne die Locale-Einstellung
    der Laufzeitumgebung zu beeinflussen."""
    sign = "+" if value > 0 else ""
    formatted = f"{value:,.2f}"  # z.B. "1,234.56"
    formatted = formatted.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{sign}{formatted}"


def build_status_report(client: TradingClient, state: BotState, trade_log_path: Path,
                         symbol: str, n_recent: int = 5) -> str:
    account = client.get_account()
    equity = float(account.equity)
    cash = float(account.cash)

    positions = client.get_all_positions()
    own_position = next((p for p in positions if p.symbol == symbol), None)

    lines = [f"Kontostand: {_money(equity)} $ (Cash: {_money(cash)} $)"]

    if own_position:
        market_value = float(own_position.market_value)
        unrealized = float(own_position.unrealized_pl)
        lines.append(
            f"Offene Position: {own_position.qty}x {symbol}, "
            f"Wert {_money(market_value)} $, unrealisiert {_money(unrealized)} $"
        )
    else:
        lines.append("Offene Position: keine")

    lines.append(
        f"Trades heute: {state.counters.trades_today}, insgesamt seit Start: {state.total_trades}"
    )
    lines.append(f"Tages-PnL: {_money(state.daily_pnl)} $, Gesamt-PnL: {_money(state.total_pnl)} $")

    today_avg = _today_averages(trade_log_path, state.trading_date)
    if today_avg:
        lines.append(f"Ø Slippage heute: {today_avg[0]:.4f} Punkte, "
                     f"Ø Lauf-Verspätung: {today_avg[1]:.2f} Min.")

    recent = _recent_trades(trade_log_path, n_recent)
    if recent:
        lines.append("")
        lines.append(f"Letzte {len(recent)} Trades:")
        lines.extend(recent)

    if state.halted_for_day:
        lines.append("")
        lines.append("Hinweis: Handel heute per Sicherheitsschalter gestoppt.")
    if state.stopped_permanently:
        lines.append("ACHTUNG: Bot dauerhaft gestoppt, manuelles Reset noetig.")

    return "\n".join(lines)


def _today_averages(trade_log_path: Path, trading_date: date | None) -> tuple[float, float] | None:
    if trade_log_path is None or trading_date is None or not trade_log_path.exists():
        return None

    with open(trade_log_path, "r", encoding="utf-8") as f:
        today_rows = [
            row for row in csv.DictReader(f)
            if row["zeitstempel"].startswith(str(trading_date))
        ]

    if not today_rows:
        return None

    avg_slippage = sum(float(r["slippage"]) for r in today_rows) / len(today_rows)
    avg_delay = sum(float(r["lauf_verspaetung_minuten"]) for r in today_rows) / len(today_rows)
    return avg_slippage, avg_delay


def _recent_trades(trade_log_path: Path, n: int) -> list[str]:
    if not trade_log_path.exists():
        return []

    with open(trade_log_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    lines = []
    for row in rows[-n:]:
        date_str = row["zeitstempel"][:10]
        pnl = float(row["pnl"])
        lines.append(f"{date_str} {row['richtung'].upper()} {row['exit_grund']} {_money(pnl)} $")
    return lines
