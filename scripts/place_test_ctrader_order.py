"""Manuell ausgeloestes Test-Skript, Pendant zu scripts/place_test_order.py
(Alpaca) fuer die cTrader Open API - siehe trading-bot-spec.md,
Aenderungsprotokoll. Nicht Teil des automatisierten Workflows.

Platziert eine winzige Test-Order (0.01 Lot, aktueller Kurs als Signal
simuliert) auf dem verbundenen Pepperstone-Demokonto, um Order-Platzierung
UND die Volumen-/Preis-Konventionen aus tradingbot/ctrader.py einmal echt
zu pruefen (beide sind laut Moduldocstring unverifiziert). Nutzt
standardmaessig das erste Symbol aus signalbot/mapping.py - lies zuerst
scripts/find_ctrader_symbols.py, falls die Symbole dort noch nicht
verifiziert sind, sonst schlaegt diese Order mit einem Symbol-Fehler fehl
(sicher, aber ohne Aussagekraft ueber die eigentliche Order-Logik)."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from signalbot.mapping import INDEX_TO_SYMBOL
from tradingbot.ctrader import (
    ctrader_session,
    get_account_info,
    get_latest_price,
    get_open_positions,
    place_market_order,
    run_ctrader,
)
from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

INSTRUMENT = INDEX_TO_SYMBOL["NASDAQ"]
NY = ZoneInfo("America/New_York")


async def main() -> None:
    async with ctrader_session() as session:
        account = await get_account_info(session)
        print(f"Account: Balance {account['balance']}")

        price = await get_latest_price(session, INSTRUMENT)
        print(f"Aktueller Kurs {INSTRUMENT}: {price}")

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
        print(f"Platziere Order: {volume} Lot {INSTRUMENT} LONG, Entry ~{signal.entry_price}, "
              f"Stop {signal.stop}, Ziel {signal.target}")

        confirm = input("Wirklich platzieren? (ja/nein): ")
        if confirm.strip().lower() != "ja":
            print("Abgebrochen.")
            return

        position_id = await place_market_order(session, INSTRUMENT, signal, volume)
        print(f"Order platziert: positionId={position_id}")

        print("\nAktuelle offene Positionen:")
        for symbol, symbol_positions in (await get_open_positions(session)).items():
            for position in symbol_positions:
                print(f"  {symbol}: {position}")


if __name__ == "__main__":
    # bewusst run_ctrader() statt asyncio.run() - siehe tradingbot/ctrader.py
    run_ctrader(main())
