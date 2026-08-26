"""EINMALIG SELBST AUSFUEHREN, NICHT Teil des automatisierten Bots.

Erzeugt eine Pyrogram-Session-Zeichenkette fuer den EIGENEN Telegram-Account.
Noetig, weil der Ziel-Signalkanal nur ueber einen Einladungslink erreichbar
ist (kein oeffentlicher Nutzername) - Bots duerfen Einladungslinks laut
Telegram grundsaetzlich nicht verwenden (getestet 26.08.2026,
BOT_METHOD_INVALID), nur echte Accounts. Siehe trading-bot-spec.md
Abschnitt 12.

Ausfuehren (im Projektverzeichnis, mit Python 3.12 wegen einer bekannten
Pyrogram-Inkompatibilitaet mit 3.14):

    py -3.12 signalbot/generate_session.py

Fragt interaktiv nach Telefonnummer (internationales Format, z.B.
+49...), dem Login-Code, den Telegram dir schickt, und - falls du die
Zwei-Schritt-Verifizierung aktiviert hast - deinem Cloud-Passwort. Alles
davon bleibt lokal in diesem Terminal, geht an niemanden sonst.

Am Ende erscheint eine lange Zeichenkette ("Session-String"). Nur DIE
weitergeben (z.B. an Claude, zum Hinterlegen als Secret) - niemals
Telefonnummer, Code oder Passwort selbst weitergeben, die werden nicht
mehr gebraucht.
"""

import os

from dotenv import load_dotenv
from pyrogram import Client

load_dotenv()

app = Client(
    "signalbot_user_session_setup",
    api_id=int(os.environ["TELEGRAM_API_ID"]),
    api_hash=os.environ["TELEGRAM_API_HASH"],
)

with app:
    session_string = app.export_session_string()
    print("\n\n=== Session-String (nur diese Zeile weitergeben) ===\n")
    print(session_string)
    print("\n=====================================================\n")
