"""Einmaliges manuelles Werkzeug, um NUR die offenen Positionen eines
bestimmten Symbols zu schliessen (im Unterschied zu
close_test_ctrader_position.py, das ALLE offenen Positionen auf dem Konto
schliesst) - fuer Faelle, in denen eine Kanal-Schliess-Anweisung fuer ein
einzelnes Instrument an einem Gemini-Fehler gescheitert ist (message_id
bereits fortgeschritten, wird vom normalen Ablauf nicht erneut versucht)
und andere Instrumente bewusst offen bleiben sollen. NICHT Teil des
automatisierten Workflows.

Symbol kommt aus der Umgebungsvariable CLOSE_SYMBOL (z.B. "NAS100")."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from tradingbot.ctrader import close_position, ctrader_session, get_open_positions, run_ctrader


async def main() -> None:
    symbol = os.environ["CLOSE_SYMBOL"]
    async with ctrader_session() as session:
        positions = await get_open_positions(session)
        symbol_positions = positions.get(symbol, [])
        if not symbol_positions:
            print(f"Keine offenen Positionen fuer {symbol} gefunden - nichts zu schliessen.")
            return

        for position in symbol_positions:
            print(f"Schliesse {symbol}: {position}")
            try:
                exit_price = await close_position(session, position["positionId"], position["volume"])
                print(f"  Geschlossen, Ausstiegspreis: {exit_price}")
            except Exception as e:
                # Siehe close_test_ctrader_position.py - close_position()
                # liefert oft nur die "Order angenommen"-Bestaetigung ohne
                # Ausstiegspreis, kein Fehlschlag der Schliessung selbst.
                print(f"  Order vermutlich angenommen, aber kein Ausstiegspreis in der Antwort: {e}")


if __name__ == "__main__":
    # bewusst run_ctrader() statt asyncio.run() - siehe tradingbot/ctrader.py
    run_ctrader(main())
