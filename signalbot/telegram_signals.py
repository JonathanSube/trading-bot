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
from datetime import datetime, timezone

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


async def _resolve_invite_link(
    app: Client, invite_hash: str, cached_peer: tuple[int, int] | None
) -> tuple[int, tuple[int, int] | None]:
    """Loest einen Einladungslink auf eine chat_id auf, die get_chat_history
    versteht. get_chat_history kann mit dem Link selbst nichts anfangen
    (versucht ihn als Nutzername/ID aufzuloesen, scheitert mit
    USERNAME_INVALID - live beobachtet 26.08.2026), und join_chat() liefert
    beim zweiten und jedem weiteren Lauf UserAlreadyParticipant statt des
    Chat-Objekts (jeder Lauf ist ein neuer, leerer in-memory-Client ohne
    Peer-Cache aus vorherigen Laeufen, siehe Modul-Docstring).

    cached_peer: (marked_id, access_hash) aus einem frueheren Lauf (siehe
    signalbot/state.py::SignalBotState.telegram_peer_id/_access_hash). Ist
    das gesetzt, wird der Peer OHNE weiteren API-Aufruf direkt in Pyrograms
    Peer-Speicher eingetragen - live beobachtet (03.09.2026): ohne diesen
    Cache ruft JEDER Lauf (im Minutentakt) CheckChatInvite erneut auf, was
    Telegram nach einer Weile als Flood wertet (FloodWait > 1500 Sekunden
    beobachtet); waehrend der Sperre schlaegt der gesamte Kanal-Abruf fehl,
    echte Handelssignale gehen dadurch verloren.

    Liefert (chat_id, neuer_cache_wert) - neuer_cache_wert ist None, wenn
    nichts frisch aufgeloest wurde (Cache-Treffer oder der seltene
    Erst-Beitritt-Zweig unten, aus dem sich kein access_hash gewinnen
    laesst) und daher der bisherige State-Wert unveraendert bleiben soll."""
    if cached_peer is not None:
        marked_id, access_hash = cached_peer
        await app.storage.update_peers([(marked_id, access_hash, "channel", None, None)])
        return marked_id, None

    result = await app.invoke(CheckChatInvite(hash=invite_hash))

    if isinstance(result, ChatInviteAlready):
        channel = result.chat
    else:
        chat = await app.join_chat(f"https://t.me/+{invite_hash}")
        return chat.id, None

    marked_id = get_channel_id(channel.id)
    await app.storage.update_peers([(marked_id, channel.access_hash, "channel", None, None)])
    return marked_id, (marked_id, channel.access_hash)


async def _resolve_chat_id(
    app: Client, channel: str, cached_peer: tuple[int, int] | None = None
) -> tuple[int | str, tuple[int, int] | None]:
    match = app.INVITE_LINK_RE.match(channel)
    if match:
        return await _resolve_invite_link(app, match.group(1), cached_peer)
    try:
        chat = await app.join_chat(channel)
        return chat.id, None
    except UserAlreadyParticipant:
        return channel, None


def _to_utc(dt: datetime | None) -> datetime | None:
    """Pyrogram liefert Zeitstempel als offset-naive datetime, der Wert
    selbst ist aber UTC - ohne tzinfo fuehrt ein Vergleich mit einem
    tz-aware now_utc sonst zu "can't subtract offset-naive and
    offset-aware datetimes"."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def fetch_new_messages(
    channel: str,
    since_message_id: int | None,
    limit: int = 50,
    cached_peer: tuple[int, int] | None = None,
) -> tuple[list[tuple[int, str, datetime]], tuple[int, int] | None]:
    """Liefert (messages, neuer_cache_wert). messages: (message_id, text,
    zeitstempel_utc) fuer alle Nachrichten neuer als since_message_id,
    chronologisch aufsteigend (aeltere zuerst) - so verarbeitet der
    Aufrufer sie in der richtigen Reihenfolge. Der Zeitstempel ist
    Telegrams eigener Sendezeitpunkt (nicht der lokale Abrufzeitpunkt) -
    Basis fuer die "seit X Minuten keine neue Nachricht"-Drosselung in
    scripts/run_signal_bot.py.

    cached_peer/neuer_cache_wert: siehe _resolve_invite_link - der
    Aufrufer soll einen frueher erhaltenen Cache-Wert hier hineinreichen
    und einen NICHT-None-Rueckgabewert dauerhaft speichern (siehe
    SignalBotState.telegram_peer_id/_access_hash), sonst droht erneut ein
    FloodWait (siehe dortiger Kommentar)."""
    app = _client()
    async with app:
        chat_id, new_cached_peer = await _resolve_chat_id(app, channel, cached_peer)

        messages: list[tuple[int, str, datetime]] = []
        async for message in app.get_chat_history(chat_id, limit=limit):
            if since_message_id is not None and message.id <= since_message_id:
                break
            text = message.text or message.caption
            if text:
                messages.append((message.id, str(text), _to_utc(message.date)))

        messages.reverse()
        return messages, new_cached_peer


async def fetch_messages_by_id(
    channel: str, message_ids: list[int], cached_peer: tuple[int, int] | None = None
) -> tuple[dict[int, tuple[str, datetime | None]], tuple[int, int] | None]:
    """Liefert (result, neuer_cache_wert). result:
    {message_id: (text, bearbeitungszeitpunkt_utc)} fuer gezielt angefragte
    Nachrichten (nicht per since_message_id-Fenster wie fetch_new_messages) -
    Nutzerwunsch (01.09.2026): der Kanal postet einen Einstieg oft zuerst
    OHNE Stop/Ziel und ergaenzt sie Sekunden spaeter per
    Nachrichten-Bearbeitung; fetch_new_messages() sieht das nicht (nur neue
    message_id, keine Edits an bereits gesehenen Nachrichten). Genutzt von
    scripts/run_signal_bot.py::_check_message_edits, um NUR die
    Quellnachrichten aktuell offener Trades gezielt erneut abzufragen statt
    die ganze Historie zu durchsuchen. edit_date ist None, wenn die
    Nachricht nie bearbeitet wurde (dann kein Grund zur erneuten Auswertung).

    cached_peer/neuer_cache_wert: siehe fetch_new_messages/
    _resolve_invite_link."""
    if not message_ids:
        return {}, None

    app = _client()
    async with app:
        chat_id, new_cached_peer = await _resolve_chat_id(app, channel, cached_peer)

        messages = await app.get_messages(chat_id, message_ids=message_ids)
        if not isinstance(messages, list):
            messages = [messages]

        result: dict[int, tuple[str, datetime | None]] = {}
        for message in messages:
            if message is None or message.empty:
                continue
            text = message.text or message.caption
            if not text:
                continue
            result[message.id] = (str(text), _to_utc(message.edit_date))
        return result, new_cached_peer
