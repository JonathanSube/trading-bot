"""Manuell ausgeloestes Test-Skript, Pendant zu scripts/place_test_order.py
(Alpaca), fuer OANDA - siehe trading-bot-spec.md, Aenderungsprotokoll zum
Umstieg des Signal-Bots auf OANDA. Nicht Teil des automatisierten
Workflows.

Platziert eine winzige Test-Bracket-Order (1 Einheit NAS100_USD, aktueller
Kurs als Signal simuliert) auf dem Practice-Account, um (a) die
Order-Platzierung aus tradingbot/oanda.py einmal echt gegen OANDA zu
pruefen und (b) die Instrument-Ticker (NAS100_USD, US30_USD, UK100_GBP,
DE30_EUR - siehe signalbot/mapping.py) zu verifizieren, bevor der
automatisierte Workflow live geht.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from tradingbot.oanda import get_account_summary, get_latest_price, get_open_trades, place_bracket_order
from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

INSTRUMENT = "NAS100_USD"
NY = ZoneInfo("America/New_York")


def main() -> None:
    account_id = os.environ["OANDA_ACCOUNT_ID"]
    summary = get_account_summary(account_id)
    print(f"Account: {account_id}, NAV: {summary['NAV']}, marginAvailable: {summary['marginAvailable']}")

    price = get_latest_price(account_id, INSTRUMENT)

    signal = Signal(
        direction=Direction.LONG,
        entry_price=price,
        stop=round(price * 0.98, 2),
        target=round(price * 1.02, 2),
        risk=round(price * 0.02, 2),
        entry_timestamp=datetime.now(NY),
    )

    units = 1  # bewusst fix auf 1 Einheit fuer den Test, nicht position_size()
    print(f"Test-Signal: {signal}")
    print(f"Platziere Bracket-Order: {units} Einheit(en) {INSTRUMENT} LONG, Entry ~{signal.entry_price}, "
          f"Stop {signal.stop}, Ziel {signal.target}")

    confirm = input("Wirklich platzieren? (ja/nein): ")
    if confirm.strip().lower() != "ja":
        print("Abgebrochen.")
        return

    trade_id = place_bracket_order(account_id, INSTRUMENT, signal, units)
    print(f"Order platziert: tradeID={trade_id}")

    print("\nAktuelle offene Trades:")
    for instrument, trade in get_open_trades(account_id).items():
        print(f"  {instrument}: {trade['currentUnits']} Einheiten, unrealisiertes P&L {trade['unrealizedPL']}")


if __name__ == "__main__":
    main()
