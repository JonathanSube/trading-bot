"""Einmaliges manuelles Werkzeug, um die Test-Position aus
scripts/place_test_ctrader_order.py wieder zu schliessen und dabei
tradingbot/ctrader.py::close_position() live zu verifizieren (letzte noch
ungetestete Funktion des Moduls, siehe trading-bot-spec.md). NICHT Teil
des automatisierten Workflows.

Schliesst ALLE aktuell offenen Positionen auf dem verbundenen Demokonto
(nicht nur eine bestimmte positionId) - fuer ein Test-/Demokonto mit nur
der einen bekannten Test-Position unproblematisch und robuster als eine
hart codierte ID, die nach dem naechsten Testkauf schon wieder veraltet
waere."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from tradingbot.ctrader import close_position, ctrader_session, get_open_positions, run_ctrader


async def main() -> None:
    async with ctrader_session() as session:
        positions = await get_open_positions(session)
        if not positions:
            print("Keine offenen Positionen gefunden - nichts zu schliessen.")
            return

        for symbol, position in positions.items():
            print(f"Schliesse {symbol}: {position}")
            exit_price = await close_position(session, position["positionId"], position["volume"])
            print(f"  Geschlossen, Ausstiegspreis: {exit_price}")


if __name__ == "__main__":
    # bewusst run_ctrader() statt asyncio.run() - siehe tradingbot/ctrader.py
    run_ctrader(main())
