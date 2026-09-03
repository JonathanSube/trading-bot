"""Einmaliges Diagnose-Skript (Nutzer-Feedback 03.09.2026: "Tom hat heute
11 Trades gemacht, der Bot aber nur 5 und genau die, die Verluste gemacht
haben") - prueft den Verdacht, dass fetch_new_messages() (siehe
signalbot/telegram_signals.py) Nachrichten OHNE Text/Caption (reine Bilder,
Sticker, Umfragen) komplett verschluckt, noch bevor sie ueberhaupt im
signal_channel_log.csv als "kein_signal" landen - dadurch waeren sie im
normalen Betrieb voellig unsichtbar. Anders als dump_channel_history.py
nutzt dieses Skript NICHT fetch_new_messages(), sondern liest den
Kanalverlauf roh (inkl. media/photo/caption-Feldern), um genau diese
Luecke sichtbar zu machen. Nicht Teil des automatisierten Workflows.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from signalbot.telegram_signals import _client, _resolve_chat_id

LOOKBACK_MESSAGES = 300


async def main() -> None:
    channel = os.environ["SIGNAL_CHANNEL"]
    app = _client()
    async with app:
        chat_id = await _resolve_chat_id(app, channel)
        async for message in app.get_chat_history(chat_id, limit=LOOKBACK_MESSAGES):
            text = message.text or message.caption
            media = message.media.value if message.media else None
            marker = "" if text else "  <-- OHNE TEXT/CAPTION (wird von fetch_new_messages() verschluckt)"
            preview = (str(text)[:80].replace("\n", " | ")) if text else ""
            print(f"{message.id}\t{message.date}\tmedia={media}\t{preview}{marker}")


if __name__ == "__main__":
    asyncio.run(main())
