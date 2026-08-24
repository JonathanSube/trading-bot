"""Benachrichtigungen bei Stopps und Tagesbericht, siehe
trading-bot-spec.md Abschnitt 3 ("Benachrichtigung senden ... simpel
halten") und Abschnitt 6 (taeglicher Statusbericht).

Telegram, weil ohne SMTP-Setup auskommt: nur ein Bot-Token und eine
Chat-ID noetig (@BotFather in Telegram, dann die eigene Chat-ID z. B. ueber
@userinfobot). Ohne gesetzte Umgebungsvariablen wird nur auf stdout
geloggt (sichtbar in den GitHub-Actions-Logs), kein Fehler.
"""

import os

import requests


def send_notification(message: str) -> None:
    print(f"[Benachrichtigung] {message}")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
    except requests.RequestException as e:
        print(f"[Benachrichtigung] Telegram-Versand fehlgeschlagen: {e}")


def get_telegram_commands(offset: int | None) -> tuple[list[str], int | None]:
    """Holt neue Telegram-Nachrichten seit offset (Kurz-Poll, kein
    Long-Polling noetig, weil ohnehin alle 5 Minuten aufgerufen). Liefert
    nur Nachrichten aus der konfigurierten Chat-ID (TELEGRAM_CHAT_ID),
    Nachrichten von anderen Chats werden ignoriert, aber trotzdem als
    verarbeitet gezaehlt (offset weiterschieben), damit sie nicht
    haengen bleiben. Gibt (Liste der Befehlstexte, neuer offset) zurueck;
    offset unveraendert, wenn Telegram nicht konfiguriert ist."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return [], offset

    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset

    try:
        resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[Benachrichtigung] Telegram-Abruf fehlgeschlagen: {e}")
        return [], offset

    commands = []
    new_offset = offset
    for update in data.get("result", []):
        new_offset = update["update_id"] + 1
        message = update.get("message", {})
        text = message.get("text", "")
        from_chat_id = str(message.get("chat", {}).get("id", ""))
        if text.startswith("/") and from_chat_id == str(chat_id):
            commands.append(text.strip())

    return commands, new_offset
