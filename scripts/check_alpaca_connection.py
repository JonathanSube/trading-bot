"""Prueft, ob die Alpaca-Zugangsdaten aus .env funktionieren und der
Paper-Endpunkt konfiguriert ist (trading-bot-spec.md, Abschnitt 7: der
Live-Endpunkt gehoert nirgends in Code oder Config).
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from alpaca.trading.client import TradingClient


def main() -> None:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    base_url = os.environ.get("ALPACA_BASE_URL", "")

    if not api_key or not secret_key:
        print("ABBRUCH: ALPACA_API_KEY / ALPACA_SECRET_KEY fehlen in .env")
        sys.exit(1)

    if "paper-api" not in base_url:
        print(f"ABBRUCH: ALPACA_BASE_URL sieht nicht nach Paper-Endpunkt aus: {base_url!r}")
        sys.exit(1)

    # paper=True ist der eigentliche Schutz (von der SDK selbst geroutet,
    # nicht von einem manuell eingetragenen String abhaengig). Die Pruefung
    # oben auf ALPACA_BASE_URL ist zusaetzliche Absicherung.
    client = TradingClient(api_key, secret_key, paper=True)
    account = client.get_account()

    print("Verbindung ok (paper=True)")
    print(f"Account-Nummer: {account.account_number}")
    print(f"Status: {account.status}")
    print(f"Cash: {account.cash}")
    print(f"Buying Power: {account.buying_power}")
    print(f"Pattern Day Trader Flag: {account.pattern_day_trader}")


if __name__ == "__main__":
    main()
