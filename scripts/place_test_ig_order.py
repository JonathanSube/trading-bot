"""Manuell ausgeloestes Test-Skript, Pendant zu scripts/place_test_order.py
(Alpaca) bzw. scripts/place_test_oanda_order.py, fuer IG - siehe
trading-bot-spec.md, Aenderungsprotokoll. Nicht Teil des automatisierten
Workflows.

Platziert eine winzige Test-Bracket-Order (1 Einheit, aktueller Kurs als
Signal simuliert) auf dem Demo-Account, um die Order-Platzierung aus
tradingbot/ig.py einmal echt gegen IG zu pruefen. Nutzt standardmaessig
den ersten Epic aus signalbot/mapping.py - lies zuerst
scripts/find_ig_epics.py, falls die Epics dort noch nicht verifiziert
sind, sonst schlaegt diese Order mit "market not found" fehl (sicher,
aber ohne Aussagekraft ueber die eigentliche Order-Logik)."""

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from signalbot.mapping import INDEX_TO_SYMBOL
from tradingbot.ig import get_account, get_latest_price, get_open_positions, login, place_bracket_order
from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

INSTRUMENT = INDEX_TO_SYMBOL["NASDAQ"]
NY = ZoneInfo("America/New_York")


def main() -> None:
    session = login()
    account = get_account(session)
    print(f"Account: {account['accountId']}, Balance: {account['balance']['balance']} "
          f"{account['currency']}")

    price = get_latest_price(session, INSTRUMENT)

    signal = Signal(
        direction=Direction.LONG,
        entry_price=price,
        stop=round(price * 0.98, 2),
        target=round(price * 1.02, 2),
        risk=round(price * 0.02, 2),
        entry_timestamp=datetime.now(NY),
    )

    size = 1  # bewusst fix auf 1 Einheit fuer den Test, nicht position_size()
    print(f"Test-Signal: {signal}")
    print(f"Platziere Bracket-Order: {size} Einheit(en) {INSTRUMENT} LONG, Entry ~{signal.entry_price}, "
          f"Stop {signal.stop}, Ziel {signal.target}")

    confirm = input("Wirklich platzieren? (ja/nein): ")
    if confirm.strip().lower() != "ja":
        print("Abgebrochen.")
        return

    deal_id = place_bracket_order(session, INSTRUMENT, signal, size, account["currency"])
    print(f"Order platziert: dealId={deal_id}")

    print("\nAktuelle offene Positionen:")
    for epic, position in get_open_positions(session).items():
        print(f"  {epic}: {position['size']} Einheiten, Level {position['level']}")


if __name__ == "__main__":
    main()
