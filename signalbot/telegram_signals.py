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
from pyrogram.raw.functions.messages import CheckChatInvite
from pyrogram.raw.types import ChatInviteAlready
from pyrogram.utils import get_channel_id


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


async def _resolve_invite_link(app: Client, invite_hash: str) -> int:
    """Loest einen Einladungslink auf eine chat_id auf, die get_chat_history
    versteht. get_chat_history kann mit dem Link selbst nichts anfangen
    (versucht ihn als Nutzername/ID aufzuloesen, scheitert mit
    USERNAME_INVALID - live beobachtet 26.08.2026), und join_chat() liefert
    beim zweiten und jedem weiteren Lauf UserAlreadyParticipant statt des
    Chat-Objekts (jeder Lauf ist ein neuer, leerer in-memory-Client ohne
    Peer-Cache aus vorherigen Laeufen, siehe Modul-Docstring).

    CheckChatInvite loest den Link unabhaengig vom Mitgliedsstatus auf und
    liefert bei bereits erfolgter Mitgliedschaft (ChatInviteAlready) das
    volle Channel-Objekt inklusive access_hash direkt mit - der wird hier
    manuell in Pyrograms Peer-Speicher eingetragen (get_channel_id fuer die
    von Pyrogram erwartete "marked" ID, siehe pyrogram.utils), weil ein
    reiner CheckChatInvite-Aufruf das nicht automatisch tut."""
    result = await app.invoke(CheckChatInvite(hash=invite_hash))

    if isinstance(result, ChatInviteAlready):
        channel = result.chat
    else:
        chat = await app.join_chat(f"https://t.me/+{invite_hash}")
        return chat.id

    marked_id = get_channel_id(channel.id)
    await app.storage.update_peers([(marked_id, channel.access_hash, "channel", None, None)])
    return marked_id


async def fetch_new_messages(channel: str, since_message_id: int | None, limit: int = 50) -> list[tuple[int, str]]:
    """Liefert (message_id, text) fuer alle Nachrichten neuer als
    since_message_id, chronologisch aufsteigend (aeltere zuerst) - so
    verarbeitet der Aufrufer sie in der richtigen Reihenfolge."""
    app = _client()
    async with app:
        match = app.INVITE_LINK_RE.match(channel)
        if match:
            chat_id = await _resolve_invite_link(app, match.group(1))
        else:
            try:
                chat = await app.join_chat(channel)
                chat_id = chat.id
            except UserAlreadyParticipant:
                chat_id = channel

        messages: list[tuple[int, str]] = []
        async for message in app.get_chat_history(chat_id, limit=limit):
            if since_message_id is not None and message.id <= since_message_id:
                break
            text = message.text or message.caption
            if text:
                messages.append((message.id, str(text)))

        messages.reverse()
        return messages
