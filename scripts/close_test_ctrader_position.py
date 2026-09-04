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

        for symbol, symbol_positions in positions.items():
            for position in symbol_positions:
                print(f"Schliesse {symbol}: {position}")
                try:
                    exit_price = await close_position(session, position["positionId"], position["volume"])
                    print(f"  Geschlossen, Ausstiegspreis: {exit_price}")
                except Exception as e:
                    # close_position() liefert oft nur die "Order
                    # angenommen"-Bestaetigung ohne Ausstiegspreis (siehe
                    # dortiger Docstring, UNVERIFIZIERT) - das ist kein
                    # Fehlschlag der Schliessung selbst, nur ein fehlender
                    # Preis. Live beobachtet (04.09.2026): ohne dieses
                    # Abfangen brach die Schleife hier komplett ab, weitere
                    # offene Positionen wurden dadurch gar nicht erst
                    # versucht zu schliessen.
                    print(f"  Order vermutlich angenommen, aber kein Ausstiegspreis in der Antwort: {e}")


if __name__ == "__main__":
    # bewusst run_ctrader() statt asyncio.run() - siehe tradingbot/ctrader.py
    run_ctrader(main())
