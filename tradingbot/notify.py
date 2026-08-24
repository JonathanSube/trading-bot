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
