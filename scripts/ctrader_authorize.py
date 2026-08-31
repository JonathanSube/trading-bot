"""EINMALIG SELBST AUSFUEHREN, NICHT Teil des automatisierten Bots.

Fuehrt den einmaligen OAuth-2.0-Autorisierungs-Ablauf der cTrader Open API
durch und liefert am Ende einen dauerhaften Refresh-Token (der Refresh-
Token selbst hat kein Ablaufdatum, siehe tradingbot/ctrader.py) - danach
holt sich der automatisierte Bot bei jedem Lauf selbststaendig einen
kurzlebigen Access-Token, kein weiterer manueller Login noetig.

Voraussetzungen (vorher selbst erledigen, siehe trading-bot-spec.md):
1. Kostenloses cTrader-Demokonto bei Pepperstone anlegen.
2. App unter https://connect.spotware.com/apps registrieren (kostenlos) -
   liefert Client ID und Client Secret. Als Redirect-URI reicht eine
   beliebige, hier nicht wirklich erreichbare URL wie
   "http://localhost/callback" - der Code wird unten manuell aus der
   Adresszeile kopiert, kein echter lokaler Server noetig.

Ausfuehren:

    CTRADER_CLIENT_ID=... CTRADER_CLIENT_SECRET=... python scripts/ctrader_authorize.py

(oder beide Werte vorher in .env eintragen). Das Skript druckt eine
Autorisierungs-URL - im Browser oeffnen, bei Pepperstone/cTrader einloggen,
das Demokonto bestaetigen. Der Browser leitet danach auf die Redirect-URI
mit einem "code"-Parameter weiter (die Seite selbst laedt nicht, das ist
normal) - den Wert aus der Adresszeile hier einfuegen. Am Ende erscheint
der Refresh-Token: NUR den als CTRADER_REFRESH_TOKEN-Secret hinterlegen,
niemals Client Secret oder Access-Token selbst weitergeben.
"""

import os
import urllib.parse

import requests
from dotenv import load_dotenv

load_dotenv()

AUTH_URL = "https://connect.spotware.com/apps/auth"
# connect.spotware.com (nicht openapi.ctrader.com) - live bestaetigt
# funktionierend, siehe tradingbot/ctrader.py zur Begruendung.
TOKEN_URL = "https://connect.spotware.com/apps/token"
REDIRECT_URI = "http://localhost/callback"


def main() -> None:
    client_id = os.environ["CTRADER_CLIENT_ID"]
    client_secret = os.environ["CTRADER_CLIENT_SECRET"]

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "trading",
    }
    print("Autorisierungs-URL im Browser oeffnen und dort einloggen:\n")
    print(f"{AUTH_URL}?{urllib.parse.urlencode(params)}\n")

    redirect_result = input(
        "Nach dem Login zeigt der Browser eine nicht ladende Seite - die volle "
        "URL aus der Adresszeile hier einfuegen (oder nur den 'code'-Wert): "
    ).strip()

    if "code=" in redirect_result:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_result).query)["code"][0]
    else:
        code = redirect_result

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    tokens = resp.json()

    print("\nErfolgreich autorisiert.")
    print(f"Refresh-Token (als CTRADER_REFRESH_TOKEN-Secret hinterlegen):\n\n{tokens['refreshToken']}\n")


if __name__ == "__main__":
    main()
