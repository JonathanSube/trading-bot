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

Du bekommst in der Nutzernachricht zwei Teile: zuerst BISHERIGE NACHRICHTEN \
(reiner Kontext, chronologisch, aelteste zuerst - NICHT selbst klassifizieren, \
nur zum Verstehen des Gespraechsverlaufs und des ueblichen Nachrichtenaufbaus \
in diesem Kanal), danach durch eine Trennzeile abgesetzt die NEUE NACHRICHT \
(die einzige, die du klassifizierst).

Aufgabe: Erkenne, ob die NEUE NACHRICHT eine Aktion fuer eines dieser vier \
Instrumente erfordert - entweder ein NEUES, eindeutiges Einstiegssignal \
("open", long oder short) ODER eine eindeutige Anweisung, eine laufende \
Position JETZT zu schliessen ("close", z. B. "CLOSE TRADE ALERT... CLOSING \
<INSTRUMENT> trade now" oder auch "closing the trade now"/"closing both \
trades now" OHNE erneut genanntes Instrument). Kommentare, Stop-Anpassungen \
an bereits laufenden Trades (z. B. "MOVING STOP TO BREAKEVEN"), Fragen, \
Werbung oder Signale zu anderen Instrumenten zaehlen NICHT als Aktion.

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

Nenne Zahlen NUR, wenn sie woertlich oder eindeutig ableitbar in der \
NEUEN NACHRICHT stehen. Erfinde NIEMALS Kurswerte oder Punktestaende - steht \
keine konkrete Zahl in der Nachricht (auch wenn ENTRY/STOP als leeres Feld \
auftauchen, z. B. "ENTRY = \n\nSTOP ="), setze entry_level, stop_level und \
target_level auf null (nicht raten oder einen plausibel klingenden Marktwert \
einsetzen) - is_signal bleibt in diesem Fall trotzdem true, wenn Instrument \
und Richtung eindeutig genannt sind. Bei Unsicherheit ueber is_signal lieber \
false setzen, statt zu raten - ein ausgelassenes Signal ist weniger schlimm \
als ein falsch interpretiertes.

WICHTIG bei Schliess-Anweisungen ohne erneut genanntes Instrument (z. B. \
"closing the trade now" oder "closing both trades now"): nutze die \
BISHERIGEN NACHRICHTEN, um das/die zuletzt eroeffneten Instrument(e) zu \
identifizieren (das juengste "BOUGHT LONG"/"SOLD SHORT" je Instrument, dem \
noch keine passende Schliess-Nachricht folgte), und setze "index" darauf - \
das ist Kontextaufloesung, kein Raten von Kurswerten (jene Regel betrifft nur \
Zahlen, nicht Instrumentnamen). Ist aus den bisherigen Nachrichten NICHT \
eindeutig erkennbar, welches Instrument gemeint ist (z. B. keine passende \
offene Position in der Historie, oder mehrdeutig), setze is_signal auf false \
und index auf null, statt zu raten. Bezieht sich "closing both trades now" \
auf zwei verschiedene Instrumente, antworte trotzdem nur mit EINEM JSON-Objekt \
fuer das zuerst genannte/naheliegendste Instrument - die Nachricht wird bei \
Bedarf erneut ausgewertet (bereits geschlossene Positionen fuehren beim \
naechsten Mal ohnehin zu keiner weiteren Aktion mehr).

Beispiele aus genau diesem Kanal (echte Nachrichten, 24.-28.08.2026 abgerufen):

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

Nachricht: "CLOSE TRADE ALERT\n\n🇺🇸 CLOSING DOW INDEX trade now"
Antwort: {"is_signal": true, "action": "close", "index": "DOW", "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: dasselbe Muster wie oben, hier fuer DOW - dieses exakte Muster ("CLOSE TRADE ALERT" + "CLOSING <INSTRUMENT> INDEX trade now") wurde live beobachtet, NACHDEM der Kanal zwei separate DOW-Long-Einstiege kurz hintereinander gepostet hatte; der Bot hatte diese Nachricht zuvor faelschlich ignoriert (Nutzer-Feedback 28.08.2026) - jetzt korrekt als Schliess-Anweisung erkannt.)

Nachricht: "move sl in dow to 53551"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: Stop-Anpassung an einem bereits laufenden Kanal-Trade, keine Schliess-Anweisung und kein neues Einstiegssignal - der Bot verwaltet seinen eigenen Stop unabhaengig vom Kanal. Trotz "sl"/"stop" im Text NICHT mit einer Schliess-Anweisung verwechseln: hier wird nur ein Stop-Preis verschoben, keine Position geschlossen.)

Nachricht: "both stopps to 53572.9"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: wie oben, hier fuer mehrere gleichzeitig laufende Kanal-eigene Trades im selben Instrument ("both") - trotzdem nur eine Stop-Anpassung, keine Schliess-Anweisung.)

Nachricht: "STOP LOSS ALERT \n\n🇩🇪 MOVING STOP TO BREAKEVEN in DAX INDEX now"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: eine Anpassung des Stops an einem bereits laufenden Kanal-Trade ist keine Schliess-Anweisung und kein neues Einstiegssignal - wird ignoriert, der Bot verwaltet seinen eigenen Stop unabhaengig vom Kanal.)

Nachricht: "+100"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: eine blosse (positive oder negative) Zahl ohne weiteren Kontext ist ein Ergebnis-/PnL-Update zu einem bereits geschlossenen Kanal-Trade, kein neues Einstiegssignal - vgl. das "-8,3"-Beispiel unten.)

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


def parse_signal_message(text: str, history: list[str] | None = None) -> dict | None:
    """Nutzt bewusst KEIN responseSchema: im Test (26.08.2026) liess das
    Modell dabei wiederholt Felder wie "direction" komplett weg, obwohl
    die Nachricht sie eindeutig enthielt (z. B. "NASDAQ INDEX long" ->
    kein "direction"-Feld in der Antwort). Ohne Schema, nur mit
    responseMimeType=application/json und dem Format in SYSTEM_PROMPT,
    lieferte dasselbe Modell in denselben Faellen vollstaendige und
    korrekte JSON-Antworten.

    history: die letzten Kanal-Nachrichten VOR `text` (chronologisch,
    aelteste zuerst), typischerweise ueber
    signalbot.channel_log.recent_message_texts() geholt - Nutzerwunsch
    (28.08.2026): Gemini soll den Gespraechsverlauf kennen, damit z. B.
    eine Schliess-Anweisung ohne erneut genanntes Instrument ("closing the
    trade now") gegen die zuletzt eroeffnete Position aufgeloest werden
    kann, statt ignoriert zu werden (siehe SYSTEM_PROMPT). Nur `text`
    selbst wird klassifiziert, `history` ist reiner Kontext.

    Liefert None nur fuer "kein API-Key konfiguriert"/"leerer Text" (kein
    Fehler, einfach nichts zu tun) - ein tatsaechlicher Gemini-Fehler
    (Netzwerk, Ratenlimit, unparsebare Antwort) loest GeminiError aus,
    damit der Aufrufer das von einer echten "kein Signal"-Klassifizierung
    unterscheiden und protokollieren/zaehlen kann (siehe
    scripts/run_signal_bot.py, signalbot/channel_log.py)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not text.strip():
        return None

    if history:
        history_block = "\n---\n".join(history)
        user_content = (
            f"BISHERIGE NACHRICHTEN (nur Kontext, nicht klassifizieren):\n{history_block}\n"
            f"=== NEUE NACHRICHT (diese klassifizieren) ===\n{text}"
        )
    else:
        user_content = text

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
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
