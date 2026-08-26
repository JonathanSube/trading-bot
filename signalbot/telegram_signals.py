"""Anbindung an den externen Telegram-Signal-Kanal, siehe
trading-bot-spec.md, Feature "Telegram-Signal-Ausfuehrung".

Nutzt Pyrogram statt der einfachen Bot-API: ein Bot kann ueber die
normale Bot-API keinem Kanal selbststaendig beitreten. Pyrogram spricht
MTProto direkt und kann das per join_chat, braucht dafuer zusaetzlich
api_id/api_hash (von https://my.telegram.org, gehoert zum selben
Bot-Konto wie TELEGRAM_BOT_TOKEN).

Laeuft "in_memory" (kein Session-File auf Platte): jeder Bot-Lauf ist
ein neuer Prozess (wie beim ORB-Bot, Abschnitt 2), ein Bot-Token-Login
ist schnell genug, um bei jedem Lauf neu zu authentifizieren, statt ein
Session-File zwischen Laeufen zu pflegen.
"""

import os

from pyrogram import Client
from pyrogram.errors import UserAlreadyParticipant


def _client() -> Client:
    return Client(
        "signalbot",
        api_id=int(os.environ["TELEGRAM_API_ID"]),
        api_hash=os.environ["TELEGRAM_API_HASH"],
        bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        in_memory=True,
    )


async def fetch_new_messages(channel: str, since_message_id: int | None, limit: int = 50) -> list[tuple[int, str]]:
    """Liefert (message_id, text) fuer alle Nachrichten neuer als
    since_message_id, chronologisch aufsteigend (aeltere zuerst) - so
    verarbeitet der Aufrufer sie in der richtigen Reihenfolge."""
    app = _client()
    async with app:
        try:
            await app.join_chat(channel)
        except UserAlreadyParticipant:
            pass

        messages: list[tuple[int, str]] = []
        async for message in app.get_chat_history(channel, limit=limit):
            if since_message_id is not None and message.id <= since_message_id:
                break
            text = message.text or message.caption
            if text:
                messages.append((message.id, str(text)))

        messages.reverse()
        return messages
