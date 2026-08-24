"""Schritt 4 (trading-bot-spec.md, Abschnitt 8): Order-Platzierung gegen
die Paper-API, manuell ausgeloest. Nicht Teil des automatisierten
Workflows, bewusst ein Skript zum Selbst-Starten.

Platziert eine winzige Test-Bracket-Order (1 Stueck QQQ, aktueller Kurs
als Signal simuliert) auf dem Paper-Account, um die Order-Platzierung aus
tradingbot/orders.py einmal echt gegen Alpaca zu pruefen. Kein Aufruf
automatisch beim Import, nur beim direkten Ausfuehren dieses Skripts.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from alpaca.trading.client import TradingClient

from tradingbot.data import load_alpaca_bars
from tradingbot.orb_strategy import Signal
from tradingbot.orders import place_bracket_order
from tradingbot.setup_detection import Direction

SYMBOL = "QQQ"
NY = ZoneInfo("America/New_York")


def main() -> None:
    client = TradingClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)
    account = client.get_account()

    bars = load_alpaca_bars(SYMBOL, lookback_days=5)
    last_close = bars[-1].close

    signal = Signal(
        direction=Direction.LONG,
        entry_price=last_close,
        stop=round(last_close * 0.98, 2),
        target=round(last_close * 1.02, 2),
        risk=round(last_close * 0.02, 2),
        entry_timestamp=datetime.now(NY),
    )

    qty = 1  # bewusst fix auf 1 Stueck fuer den Test, nicht position_size()
    print(f"Account: {account.account_number}, Cash: {account.cash}, Buying Power: {account.buying_power}")
    print(f"Test-Signal: {signal}")
    print(f"Platziere Bracket-Order: {qty} Stueck {SYMBOL} LONG, Entry ~{signal.entry_price}, "
          f"Stop {signal.stop}, Ziel {signal.target}")

    confirm = input("Wirklich platzieren? (ja/nein): ")
    if confirm.strip().lower() != "ja":
        print("Abgebrochen.")
        return

    order = place_bracket_order(client, SYMBOL, signal, qty)
    print(f"Order platziert: id={order.id}, status={order.status}")

    print("\nAktuelle offene Positionen:")
    for pos in client.get_all_positions():
        print(f"  {pos.symbol}: {pos.qty} Stueck, unrealisiertes P&L {pos.unrealized_pl}")


if __name__ == "__main__":
    main()
