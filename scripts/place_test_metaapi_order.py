"""Manuell ausgeloestes Test-Skript, Pendant zu scripts/place_test_order.py
(Alpaca) fuer MetaApi.cloud - siehe trading-bot-spec.md,
Aenderungsprotokoll. Nicht Teil des automatisierten Workflows.

Platziert eine winzige Test-Bracket-Order (0.01 Lot, aktueller Kurs als
Signal simuliert) auf dem verbundenen MT4/5-Demokonto, um die
Order-Platzierung aus tradingbot/metaapi.py einmal echt zu pruefen. Nutzt
standardmaessig das erste Symbol aus signalbot/mapping.py - lies zuerst
scripts/find_metaapi_symbols.py, falls die Symbole dort noch nicht
verifiziert sind, sonst schlaegt diese Order mit "symbol not found" fehl
(sicher, aber ohne Aussagekraft ueber die eigentliche Order-Logik)."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from signalbot.mapping import INDEX_TO_SYMBOL
from tradingbot.metaapi import get_account, get_latest_price, get_open_positions, place_bracket_order
from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

INSTRUMENT = INDEX_TO_SYMBOL["NASDAQ"]
NY = ZoneInfo("America/New_York")


def main() -> None:
    account = get_account()
    print(f"Account: {account.get('name')}, Balance: {account['balance']} {account['currency']}")

    price = get_latest_price(INSTRUMENT)

    signal = Signal(
        direction=Direction.LONG,
        entry_price=price,
        stop=round(price * 0.98, 2),
        target=round(price * 1.02, 2),
        risk=round(price * 0.02, 2),
        entry_timestamp=datetime.now(NY),
    )

    volume = 0.01  # bewusst fix auf die Mindestgroesse fuer den Test, nicht position_size()
    print(f"Test-Signal: {signal}")
    print(f"Platziere Bracket-Order: {volume} Lot {INSTRUMENT} LONG, Entry ~{signal.entry_price}, "
          f"Stop {signal.stop}, Ziel {signal.target}")

    confirm = input("Wirklich platzieren? (ja/nein): ")
    if confirm.strip().lower() != "ja":
        print("Abgebrochen.")
        return

    position_id = place_bracket_order(INSTRUMENT, signal, volume)
    print(f"Order platziert: positionId={position_id}")

    print("\nAktuelle offene Positionen:")
    for symbol, position in get_open_positions().items():
        print(f"  {symbol}: {position['volume']} Lot, Kurs {position['openPrice']}")


if __name__ == "__main__":
    main()
