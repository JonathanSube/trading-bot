"""Anbindung an den externen Telegram-Signal-Kanal, siehe
trading-bot-spec.md, Feature "Telegram-Signal-Ausfuehrung".

Nutzt Pyrogram statt der einfachen Bot-API: ein Bot kann ueber die
normale Bot-API keinem Kanal selbststaendig beitreten. Pyrogram spricht
MTProto direkt.

Zwei Modi, je nachdem was gesetzt ist:
- TELEGRAM_USER_SESSION gesetzt: eigener Account (Session-String, siehe
  signalbot/generate_session.py). Noetig fuer Kanaele, die nur per
  Einladungslink erreichbar sind - Bots duerfen Einladungslinks laut
  Telegram grundsaetzlich nicht verwenden (getestet 26.08.2026,
  BOT_METHOD_INVALID beim Zielkanal dieses Projekts).
- sonst: Bot-Token (gleicher Bot wie fuer Benachrichtigungen). Funktioniert
  nur bei Kanaelen mit oeffentlichem Nutzernamen (join per Namen, nicht
  per Einladungslink).

Laeuft "in_memory" (kein Session-File auf Platte): jeder Bot-Lauf ist
ein neuer Prozess (wie beim ORB-Bot, Abschnitt 2), ein Login ueber
Token/Session-String ist schnell genug, um bei jedem Lauf neu zu
authentifizieren, statt ein Session-File zwischen Laeufen zu pflegen.
"""

import os

from pyrogram import Client
from pyrogram.errors import UserAlreadyParticipant


def _client() -> Client:
    session_string = os.environ.get("TELEGRAM_USER_SESSION")
    if session_string:
        return Client(
            "signalbot",
            api_id=int(os.environ["TELEGRAM_API_ID"]),
            api_hash=os.environ["TELEGRAM_API_HASH"],
            session_string=session_string,
            in_memory=True,
        )
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
