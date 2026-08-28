"""Manuell ausgeloestes Diagnose-Skript, nicht Teil des automatisierten
Workflows - gleiche Kategorie wie scripts/place_test_order.py. Listet alle
beim verbundenen MT4/5-Broker-Server verfuegbaren Symbole und filtert auf
Stichwoerter fuer NASDAQ/DOW/FTSE/DAX, weil die in signalbot/mapping.py
hinterlegten Symbolnamen unverifizierte Platzhalter sind (MetaApis
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

from tradingbot.metaapi import list_symbols

KEYWORDS = {
    "NASDAQ": ["NAS100", "NASDAQ", "USTEC", "NDX"],
    "DOW": ["US30", "DOW", "WS30"],
    "FTSE": ["UK100", "FTSE"],
    "DAX": ["DAX", "GER40", "DE40", "GERMANY40"],
}


def main() -> None:
    symbols = list_symbols()
    for index_name, keywords in KEYWORDS.items():
        print(f"\n=== {index_name} ===")
        matches = [s for s in symbols if any(kw.upper() in s.upper() for kw in keywords)]
        if not matches:
            print("  keine Treffer")
            continue
        for symbol in matches:
            print(f"  {symbol!r}")


if __name__ == "__main__":
    main()
