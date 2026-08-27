"""Manuell ausgeloestes Diagnose-Skript, nicht Teil des automatisierten
Workflows - gleiche Kategorie wie scripts/place_test_order.py. Sucht die
tatsaechlichen IG-Epic-Codes fuer NASDAQ/DOW/FTSE/DAX gegen den echten
Demo-Account, weil die in signalbot/mapping.py hinterlegten Epics
unverifizierte Platzhalter sind (IGs API-Doku war in dieser Umgebung
nicht abrufbar - siehe trading-bot-spec.md, Aenderungsprotokoll).

Ausgabe: fuer jeden Suchbegriff alle Treffer (Epic, Name, Markttyp) - die
passenden Epics manuell in signalbot/mapping.py::INDEX_TO_SYMBOL
uebertragen.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from tradingbot.ig import login, search_markets

SEARCH_TERMS = {
    "NASDAQ": ["US Tech 100", "Nasdaq 100", "US Nasdaq"],
    "DOW": ["Wall Street", "Dow Jones", "US 30"],
    "FTSE": ["FTSE 100", "UK 100"],
    "DAX": ["Germany 40", "DAX", "Germany 30"],
}


def main() -> None:
    session = login()
    for index_name, terms in SEARCH_TERMS.items():
        print(f"\n=== {index_name} ===")
        for term in terms:
            markets = search_markets(session, term)
            if not markets:
                print(f"  '{term}': keine Treffer")
                continue
            for market in markets:
                print(f"  '{term}' -> epic={market['epic']!r}  name={market['instrumentName']!r}  "
                      f"type={market.get('instrumentType')}  expiry={market.get('expiry')}")


if __name__ == "__main__":
    main()
