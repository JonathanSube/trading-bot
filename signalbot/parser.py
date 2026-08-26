"""LLM-Auswertung der Kanal-Nachrichten per Google Gemini (kostenloses
Freikontingent, siehe trading-bot-spec.md, Feature
"Telegram-Signal-Ausfuehrung"). Reiner REST-Aufruf statt eines eigenen
SDK, konsistent mit dem Rest des Projekts (tradingbot/notify.py nutzt
fuer Telegram ebenfalls requests statt eines Client-Pakets).

Liefert None bei jedem Fehler (keine API-Key gesetzt, Netzwerkfehler,
unparsebare Antwort) - der Aufrufer behandelt das wie "kein Signal
erkannt", nicht wie einen Absturz. Ein einzelner LLM-Ausfall soll nie
den ganzen Lauf zum Scheitern bringen.
"""

import json
import os

import requests

GEMINI_MODEL = "gemini-3.6-flash"  # 3.5-flash-lite erfand teils falsche/fehlende Felder im Test, siehe unten
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

SYSTEM_PROMPT = """Du wertest Nachrichten aus einem oeffentlichen Telegram-Kanal fuer \
Live-Day-Trading-Signale aus. Der Kanal handelt unter anderem NASDAQ INDEX und \
DOW JONES INDEX.

Aufgabe: Erkenne, ob die Nachricht ein NEUES, eindeutiges Einstiegssignal fuer \
NASDAQ INDEX oder DOW JONES INDEX enthaelt (long oder short). Kommentare, \
Ergebnis-Updates zu bereits laufenden Trades, Fragen, Werbung oder Signale zu \
anderen Instrumenten zaehlen NICHT als neues Einstiegssignal.

Antworte ausschliesslich als JSON nach folgendem Schema:
{
  "is_signal": true/false,
  "index": "NASDAQ" | "DOW" | null,
  "direction": "long" | "short" | null,
  "entry_level": Zahl | null,
  "stop_level": Zahl | null,
  "target_level": Zahl | null
}

Nenne Zahlen NUR, wenn sie woertlich oder eindeutig ableitbar in der Nachricht \
stehen. Erfinde NIEMALS Kurswerte oder Punktestaende - steht keine konkrete Zahl \
in der Nachricht, setze entry_level, stop_level und target_level auf null (nicht \
raten oder einen plausibel klingenden Marktwert einsetzen). Bei Unsicherheit \
ueber is_signal lieber false setzen, statt zu raten - ein ausgelassenes Signal ist \
weniger schlimm als ein falsch interpretiertes."""

def parse_signal_message(text: str) -> dict | None:
    """Nutzt bewusst KEIN responseSchema: im Test (26.08.2026) liess das
    Modell dabei wiederholt Felder wie "direction" komplett weg, obwohl
    die Nachricht sie eindeutig enthielt (z. B. "NASDAQ INDEX long" ->
    kein "direction"-Feld in der Antwort). Ohne Schema, nur mit
    responseMimeType=application/json und dem Format in SYSTEM_PROMPT,
    lieferte dasselbe Modell in denselben Faellen vollstaendige und
    korrekte JSON-Antworten."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not text.strip():
        return None

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    try:
        resp = requests.post(GEMINI_URL, params={"key": api_key}, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        text_out = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text_out)
    except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as e:
        print(f"[SignalBot] Gemini-Auswertung fehlgeschlagen: {e}")
        return None
