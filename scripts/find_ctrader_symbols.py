"""Manuell ausgeloestes Diagnose-Skript, nicht Teil des automatisierten
Workflows - gleiche Kategorie wie scripts/place_test_order.py. Listet alle
beim verbundenen Pepperstone-Demokonto verfuegbaren Symbole und filtert auf
Stichwoerter fuer NASDAQ/DOW/FTSE/DAX, weil die in signalbot/mapping.py
hinterlegten Symbolnamen unverifizierte Platzhalter sind (cTraders
API-Doku war in dieser Umgebung nicht abrufbar - siehe
trading-bot-spec.md, Aenderungsprotokoll).

Ausgabe: fuer jedes Stichwort alle passenden Symbole - das jeweils
richtige manuell in signalbot/mapping.py::INDEX_TO_SYMBOL uebertragen.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from tradingbot.ctrader import ctrader_session, list_symbols, run_ctrader

KEYWORDS = {
    "NASDAQ": ["NAS100", "NASDAQ", "USTEC", "NDX"],
    "DOW": ["US30", "DOW", "WS30"],
    "FTSE": ["UK100", "FTSE"],
    "DAX": ["DAX", "GER40", "DE40", "GERMANY40"],
}


async def main() -> None:
    async with ctrader_session() as session:
        symbols = await list_symbols(session)
        for index_name, keywords in KEYWORDS.items():
            print(f"\n=== {index_name} ===")
            matches = [name for name in symbols if any(kw.upper() in name.upper() for kw in keywords)]
            if not matches:
                print("  keine Treffer")
                continue
            for name in matches:
                print(f"  {name!r} (symbolId={symbols[name]})")


if __name__ == "__main__":
    # bewusst run_ctrader() statt asyncio.run() - siehe tradingbot/ctrader.py
    run_ctrader(main())
