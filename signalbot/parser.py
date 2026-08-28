"""LLM-Auswertung der Kanal-Nachrichten per Google Gemini (kostenloses
Freikontingent, siehe trading-bot-spec.md, Feature
"Telegram-Signal-Ausfuehrung"). Reiner REST-Aufruf statt eines eigenen
SDK, konsistent mit dem Rest des Projekts (tradingbot/notify.py nutzt
fuer Telegram ebenfalls requests statt eines Client-Pakets).

Liefert None nur, wenn kein API-Key gesetzt ist oder der Text leer ist
(kein Fehler, einfach nichts zu tun) - ein echter Gemini-Fehler
(Netzwerk, Ratenlimit, unparsebare Antwort) loest stattdessen GeminiError
aus, siehe dortige Docstring zur Begruendung (Nutzer-Feedback 28.08.2026:
verschluckte Fehler sahen identisch aus wie echte Nicht-Signale, dadurch
war nicht erkennbar, warum der Bot nur 1 von ueber 5 gesendeten Signalen
beachtet hatte).
"""

import json
import os

import requests

GEMINI_MODEL = "gemini-flash-lite-latest"  # siehe trading-bot-spec.md Abschnitt 12 fuer die Modell-Historie
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

SYSTEM_PROMPT = """Du wertest Nachrichten aus einem oeffentlichen Telegram-Kanal fuer \
Live-Day-Trading-Signale aus. Der Kanal handelt NASDAQ INDEX, DOW JONES INDEX, \
GERMAN DAX INDEX und FTSE 100 INDEX (jeweils mit einem Flaggen-Emoji vor dem \
Namen, das ignoriert werden kann).

Aufgabe: Erkenne, ob die Nachricht eine Aktion fuer eines dieser vier \
Instrumente erfordert - entweder ein NEUES, eindeutiges Einstiegssignal \
("open", long oder short) ODER eine eindeutige Anweisung, eine laufende \
Position in einem bestimmten Instrument JETZT zu schliessen ("close", z. B. \
"CLOSE TRADE ALERT... CLOSING <INSTRUMENT> trade now"). Kommentare, \
Stop-Anpassungen an bereits laufenden Trades (z. B. "MOVING STOP TO \
BREAKEVEN"), Fragen, Werbung oder Signale zu anderen Instrumenten zaehlen \
NICHT als Aktion.

Antworte ausschliesslich als JSON nach folgendem Schema:
{
  "is_signal": true/false,
  "action": "open" | "close" | null,
  "index": "NASDAQ" | "DOW" | "DAX" | "FTSE" | null,
  "direction": "long" | "short" | null,
  "entry_level": Zahl | null,
  "stop_level": Zahl | null,
  "target_level": Zahl | null
}

Bei "action": "close" bleiben direction/entry_level/stop_level/target_level \
null (nicht relevant fuer eine Schliess-Anweisung).

Nenne Zahlen NUR, wenn sie woertlich oder eindeutig ableitbar in der Nachricht \
stehen. Erfinde NIEMALS Kurswerte oder Punktestaende - steht keine konkrete Zahl \
in der Nachricht (auch wenn ENTRY/STOP als leeres Feld auftauchen, z. B. "ENTRY = \
\n\nSTOP ="), setze entry_level, stop_level und target_level auf null (nicht raten \
oder einen plausibel klingenden Marktwert einsetzen) - is_signal bleibt in diesem \
Fall trotzdem true, wenn Instrument und Richtung eindeutig genannt sind. Bei \
Unsicherheit ueber is_signal lieber false setzen, statt zu raten - ein \
ausgelassenes Signal ist weniger schlimm als ein falsch interpretiertes.

Nennt die Nachricht KEIN konkretes Instrument bei einer Schliess-Anweisung \
(z. B. "closing both trades now" ohne Instrumentname), setze is_signal auf \
false und index auf null - das Instrument darf nicht geraten werden (siehe \
Regel oben). Ein solcher Fall ist noch nicht abschliessend im Prompt \
abgedeckt (fehlende reale Beispielnachricht), wird aber protokolliert \
(signalbot/channel_log.py) und spaeter nachgezogen, sobald ein echtes \
Beispiel vorliegt.

Beispiele aus genau diesem Kanal (echte Nachrichten, 24.-27.08.2026 abgerufen):

Nachricht: "🇺🇸 NASDAQ INDEX\nBOUGHT LONG ⬆️️ = 100%\n\nENTRY = 29247.8\n\nSTOP = 29180.6"
Antwort: {"is_signal": true, "action": "open", "index": "NASDAQ", "direction": "long", "entry_level": 29247.8, "stop_level": 29180.6, "target_level": null}

Nachricht: "🇺🇸 DOW JONES INDEX\nBOUGHT LONG ⬆️️ = 100%\n\nENTRY = 53327.5\n\nSTOP = 53227.3"
Antwort: {"is_signal": true, "action": "open", "index": "DOW", "direction": "long", "entry_level": 53327.5, "stop_level": 53227.3, "target_level": null}

Nachricht: "🇩🇪  GERMAN DAX INDEX\nSOLD SHORT 🔻 = 50%\n\nENTRY = 26057.5\n\nSTOP = 26099.4"
Antwort: {"is_signal": true, "action": "open", "index": "DAX", "direction": "short", "entry_level": 26057.5, "stop_level": 26099.4, "target_level": null}

Nachricht: "🇬🇧 FTSE 100 INDEX\nBOUGHT LONG ⬆️️ = 50%\n\nENTRY = 10865.8\n\nSTOP = 10840.8"
Antwort: {"is_signal": true, "action": "open", "index": "FTSE", "direction": "long", "entry_level": 10865.8, "stop_level": 10840.8, "target_level": null}

Nachricht: "🇺🇸 DOW JONES INDEX\nBOUGHT LONG ⬆️️ = 100%\n\nENTRY = \n\nSTOP ="
Antwort: {"is_signal": true, "action": "open", "index": "DOW", "direction": "long", "entry_level": null, "stop_level": null, "target_level": null}
(Grund: Instrument und Richtung sind eindeutig, die Level-Felder sind nur leer, weil der Kanal sie zum Sendezeitpunkt noch nicht kannte - trotzdem ein gueltiges Einstiegssignal.)

Nachricht: "CLOSE TRADE ALERT \n\n🇩🇪 CLOSING DAX INDEX trade now"
Antwort: {"is_signal": true, "action": "close", "index": "DAX", "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: eindeutige Anweisung, die laufende DAX-Position JETZT zu schliessen - ein konkretes Instrument wird genannt, also handelbar.)

Nachricht: "STOP LOSS ALERT \n\n🇩🇪 MOVING STOP TO BREAKEVEN in DAX INDEX now"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: eine Anpassung des Stops an einem bereits laufenden Kanal-Trade ist keine Schliess-Anweisung und kein neues Einstiegssignal - wird ignoriert, der Bot verwaltet seinen eigenen Stop unabhaengig vom Kanal.)

Nachricht: "no open orders, no open positions"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}

Nachricht: "STATUS"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}

Nachricht: "-8,3"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: eine blosse Zahl ohne Kontext ist vermutlich ein Ergebnis-/PnL-Update, kein neues Einstiegssignal.)"""

class GeminiError(RuntimeError):
    """Der Gemini-Aufruf selbst ist fehlgeschlagen (Netzwerk, Ratenlimit,
    unparsebare Antwort) - bewusst von "Nachricht ist kein Signal"
    unterschieden (siehe Rueckgabewert unten), sonst verschwinden echte
    Signale bei einem API-Ausfall stillschweigend im selben Pfad wie
    Werbe-/Kommentar-Nachrichten. Nutzer-Feedback (28.08.2026): der Bot
    hatte nur 1 von ueber 5 gesendeten Signalen beachtet - ohne diese
    Unterscheidung war nicht erkennbar, ob das an echten Nicht-Signalen
    oder an verschluckten API-Fehlern lag."""


def parse_signal_message(text: str) -> dict | None:
    """Nutzt bewusst KEIN responseSchema: im Test (26.08.2026) liess das
    Modell dabei wiederholt Felder wie "direction" komplett weg, obwohl
    die Nachricht sie eindeutig enthielt (z. B. "NASDAQ INDEX long" ->
    kein "direction"-Feld in der Antwort). Ohne Schema, nur mit
    responseMimeType=application/json und dem Format in SYSTEM_PROMPT,
    lieferte dasselbe Modell in denselben Faellen vollstaendige und
    korrekte JSON-Antworten.

    Liefert None nur fuer "kein API-Key konfiguriert"/"leerer Text" (kein
    Fehler, einfach nichts zu tun) - ein tatsaechlicher Gemini-Fehler
    (Netzwerk, Ratenlimit, unparsebare Antwort) loest GeminiError aus,
    damit der Aufrufer das von einer echten "kein Signal"-Klassifizierung
    unterscheiden und protokollieren/zaehlen kann (siehe
    scripts/run_signal_bot.py, signalbot/channel_log.py)."""
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
        raise GeminiError(str(e)) from e
