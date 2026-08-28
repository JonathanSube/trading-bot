"""Manuell ausgeloestes Diagnose-Skript, nicht Teil des automatisierten
Workflows - gleiche Kategorie wie scripts/place_test_order.py. Listet alle
beim verbundenen MT4/5-Broker-Server verfuegbaren Symbole und filtert auf
Stichwoerter fuer NASDAQ/DOW/FTSE/DAX, weil die in signalbot/mapping.py
hinterlegten Symbolnamen unverifizierte Platzhalter sind (MetaApis
API-Doku war in dieser Umgebung nicht abrufbar - siehe
trading-bot-spec.md, Aenderungsprotokoll).

Zuerst list_accounts() (braucht nur METAAPI_TOKEN, keine
METAAPI_ACCOUNT_ID) - Diagnose-Hilfe, falls die Account-ID im Dashboard
nicht auffindbar war (Nutzer-Feedback 28.08.2026): zeigt alle zum Token
gehoerenden Accounts inkl. ihrer echten IDs, bevor ueberhaupt versucht
wird, Symbole abzufragen.

Ausgabe: fuer jedes Stichwort alle passenden Symbole - das jeweils
richtige manuell in signalbot/mapping.py::INDEX_TO_SYMBOL uebertragen.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from tradingbot.metaapi import list_accounts, list_symbols

KEYWORDS = {
    "NASDAQ": ["NAS100", "NASDAQ", "USTEC", "NDX"],
    "DOW": ["US30", "DOW", "WS30"],
    "FTSE": ["UK100", "FTSE"],
    "DAX": ["DAX", "GER40", "DE40", "GERMANY40"],
}


def main() -> None:
    accounts = list_accounts()
    print(f"=== Accounts fuer diesen METAAPI_TOKEN ({len(accounts)}) ===")
    for account in accounts:
        print(f"  id={account.get('id')!r}  name={account.get('name')!r}  "
              f"type={account.get('type')!r}  region={account.get('region')!r}  "
              f"state={account.get('state')!r}  platform={account.get('platform')!r}")
    if not accounts:
        print("  keine Accounts gefunden - Token pruefen (evtl. auf ein anderes "
              "MetaApi-Projekt/Konto ausgestellt) oder erst einen Trading-Account "
              "im Dashboard anlegen.")
        return

    configured_id = os.environ.get("METAAPI_ACCOUNT_ID")
    account_ids = {a.get("id") for a in accounts}
    if configured_id and configured_id not in account_ids:
        print(f"\nWARNUNG: METAAPI_ACCOUNT_ID ({configured_id!r}) ist in obiger "
              f"Liste nicht enthalten - vermutlich der Grund fuer den 404-Fehler. "
              f"Verwende stattdessen den ersten gefundenen Account fuer die "
              f"Symbolsuche unten.")
    if not configured_id or configured_id not in account_ids:
        os.environ["METAAPI_ACCOUNT_ID"] = accounts[0]["id"]

    print(f"\n=== Symbolsuche (Account {os.environ['METAAPI_ACCOUNT_ID']!r}) ===")
    symbols = list_symbols()
    for index_name, keywords in KEYWORDS.items():
        print(f"\n--- {index_name} ---")
        matches = [s for s in symbols if any(kw.upper() in s.upper() for kw in keywords)]
        if not matches:
            print("  keine Treffer")
            continue
        for symbol in matches:
            print(f"  {symbol!r}")


if __name__ == "__main__":
    main()
