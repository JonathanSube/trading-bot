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
import re
import time

import requests

GEMINI_MODEL = "gemini-flash-lite-latest"  # siehe trading-bot-spec.md Abschnitt 12 fuer die Modell-Historie
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Live beobachtet (01.09.2026): ein einzelner Gemini-Aufruf lief in einen
# Read-Timeout (20s), die Nachricht wurde daraufhin OHNE erneuten Versuch
# als "kein Signal" behandelt - ein direkt folgendes, zweites Signal in
# derselben Kanal-Nachrichtenserie wurde normal gehandelt. Ohne Retry geht
# so ein ganzes Signal verloren, nur weil ein einzelner API-Aufruf
# voruebergehend haengt - analog zum ACCESS_DENIED-Retry bei cTrader
# (siehe tradingbot/ctrader.py::get_access_token).
#
# Auf 3 erhoeht (02.09.2026): trotz 2 Versuchen ging eine eindeutige
# Schliess-Nachricht ("CLOSED FTSE.... MINUS 8 and MINUS 5") erneut per
# gemini_fehler verloren - die betroffene Position wurde dadurch nicht per
# Kanal-Anweisung, sondern erst spaeter ueber den eigenen (weiter
# entfernten) Stop geschlossen, mit groesserem Verlust als noetig.
GEMINI_RETRY_ATTEMPTS = 3
GEMINI_RETRY_DELAY_SECONDS = 3

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
("open", long oder short) ODER eine eindeutige Anweisung/Mitteilung, dass \
eine laufende Position JETZT geschlossen ist bzw. werden soll ("close"). \
Das umfasst sowohl ausdrueckliche Anweisungen ("CLOSE TRADE ALERT... \
CLOSING <INSTRUMENT> trade now", "closing the trade now"/"closing both \
trades now" OHNE erneut genanntes Instrument) ALS AUCH Ergebnis-Mitteilungen \
MIT genanntem Instrument, die belegen, dass der Kanal seine eigene Position \
bereits beendet hat (z. B. "STOPPED OUT OF <INSTRUMENT> MINUS 125", \
"<INSTRUMENT> HIT TARGET +200", "closed <INSTRUMENT> for +50") - auch wenn \
kein woertliches "close"/"closing" vorkommt: der Bot bildet die \
Kanal-Trades nach, sobald der Kanal seinen eigenen Trade fuer beendet \
erklaert, MUSS die eigene (mitgelaufene) Position ebenfalls beendet werden, \
sonst laeuft sie unkontrolliert gegen den eigenen Stop weiter (live \
beobachtet 01.09.2026: eine ignorierte "STOPPED OUT OF DOW MINUS 125"-\
Nachricht liess die eigene Position 64 Minuten laenger als noetig offen, \
Verlust 1,75x statt der von Tom tatsaechlich realisierten ~1x). Eine blosse \
Zahl OHNE genanntes Instrument (z. B. "+100") bleibt weiterhin nicht \
handelbar, siehe Beispiel unten - dort ist nicht eindeutig, welche Position \
gemeint ist. Kommentare, Stop-Anpassungen an bereits laufenden Trades (z. B. \
"MOVING STOP TO BREAKEVEN"), Fragen, Werbung oder Signale zu anderen \
Instrumenten zaehlen NICHT als Aktion.

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

Nachricht: "STOPPED OUT OF DOW MINUS 125"
Antwort: {"is_signal": true, "action": "close", "index": "DOW", "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: eindeutige Ergebnis-Mitteilung MIT genanntem Instrument - der Kanal hat seine eigene DOW-Position bereits beendet (per Stop), also muss auch die eigene mitgelaufene Position jetzt geschlossen werden, obwohl kein woertliches "close"/"closing" vorkommt. Live beobachtet (01.09.2026): diese Nachricht wurde zuvor faelschlich als "kein_signal" behandelt, die eigene Position lief dadurch 64 Minuten unnoetig weiter und produzierte einen 1,75x groesseren Verlust als der Kanal selbst hatte.)

Nachricht: "NASDAQ HIT TARGET +180"
Antwort: {"is_signal": true, "action": "close", "index": "NASDAQ", "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: dasselbe Muster wie oben, hier fuer einen Gewinn-Abschluss statt eines Stops - auch das ist eine abgeschlossene Position, kein neues Signal.)

Nachricht: "+100"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: eine blosse (positive oder negative) Zahl OHNE genanntes Instrument ist nicht eindeutig zuordenbar - anders als bei "STOPPED OUT OF DOW..." oben fehlt hier das Instrument, also kein handelbares Signal, vgl. das "-8,3"-Beispiel unten.)

Nachricht: "no open orders, no open positions"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}

Nachricht: "STATUS"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}

Nachricht: "-8,3"
Antwort: {"is_signal": false, "action": null, "index": null, "direction": null, "entry_level": null, "stop_level": null, "target_level": null}
(Grund: eine blosse Zahl ohne Kontext ist vermutlich ein Ergebnis-/PnL-Update, kein neues Einstiegssignal.)"""

# Regelbasierte Schnellerkennung (02.09.2026, Nutzerwunsch: "in Sekunden
# reagieren", nachdem Gemini-Latenz/-Timeouts mehrfach echte Signale
# verzoegert oder verpasst haben, siehe GEMINI_RETRY_ATTEMPTS oben). Die
# grosse Mehrheit der Kanal-Nachrichten folgt einem von zwei starren
# Mustern (Einstieg mit INDEX-Kopfzeile + BOUGHT LONG/SOLD SHORT +
# ENTRY=/STOP=, oder eine Schliess-Mitteilung mit eindeutigem
# Schluesselwort) - die lassen sich ohne jeden Netzwerk-Aufruf zuverlaessig
# erkennen. Bewusst KONSERVATIV: nur bei eindeutigem Treffer (genau EIN
# erkanntes Instrument, klar erkennbares Muster, keine widerspruechliche
# Kombination aus Einstiegs- und Schliess-Indizien) wird ueberhaupt ein
# Ergebnis geliefert - in jedem unklaren Fall None, dann greift wie bisher
# Gemini (inkl. der Kontextaufloesung fuer instrumentlose Schliess-
# Nachrichten wie "closing the trade now", die dieser Schnellweg bewusst
# NICHT behandelt, da er keine Historie kennt).
_INDEX_ALIASES = [
    (re.compile(r"\bNASDAQ\b", re.IGNORECASE), "NASDAQ"),
    (re.compile(r"\bDOW\b", re.IGNORECASE), "DOW"),
    (re.compile(r"\bDAX\b", re.IGNORECASE), "DAX"),
    (re.compile(r"\bFTSE\b", re.IGNORECASE), "FTSE"),
]

_OPEN_RE = re.compile(
    r"\bINDEX\b.*?\b(BOUGHT\s+LONG|SOLD\s+SHORT)\b.*?ENTRY\s*=\s*([\d,\.]*)\s*.*?STOP\s*=\s*([\d,\.]*)",
    re.IGNORECASE | re.DOTALL,
)

# Jedes einzelne Muster hier wurde live in genau diesem Kanal beobachtet
# (siehe trading-bot-spec.md, Aenderungsprotokoll 01./02.09.2026) - keine
# geratenen Formulierungen.
_CLOSE_RE = re.compile(
    r"(\bSTOPPED\s+OUT\s+OF\b"
    r"|\bCLOSE\s+TRADE\s+ALERT\b"
    r"|\bTRADE[\s-]*CLOSE\s+ALERT\b"
    r"|^\s*CLOSED\b"
    r"|\bCLOSING\s+\S+(?:\s+\S+)?\s+INDEX\s+trade\s+now\b"
    r"|\bHIT\s+TARGET\b)",
    re.IGNORECASE,
)


def _single_index(text: str) -> str | None:
    """Nur bei GENAU einem erkannten Instrument eindeutig - bei null oder
    mehreren wird lieber Gemini gefragt (bzw. bei mehreren gleichzeitig
    genannten Instrumenten ist ohnehin nicht klar, welches gemeint ist)."""
    found = {name for pattern, name in _INDEX_ALIASES if pattern.search(text)}
    return found.pop() if len(found) == 1 else None


def mentioned_indices(text: str) -> list[str]:
    """Alle im Rohtext erwaehnten Instrumente, Reihenfolge des ersten
    Vorkommens - fuer Schliess-Nachrichten mit MEHREREN gleichzeitig
    genannten Instrumenten (z. B. "CLOSED DOW AND NASDAQ"). Sowohl
    _fast_parse (ueber _single_index) als auch Gemini (siehe SYSTEM_PROMPT)
    liefern pro Nachricht bewusst nur EIN "index"-Feld zurueck - live
    beobachtet (02.09.2026): bei "CLOSED DOW AND NASDAQ" wurde dadurch nur
    DOW geschlossen, die NASDAQ-Position (NAS100) blieb faelschlich offen,
    obwohl der Kanal beide fuer beendet erklaert hatte. Der Aufrufer
    (scripts/run_signal_bot.py) nutzt dies zusaetzlich zum geparsten
    "index", um bei einer erkannten Schliess-Anweisung ALLE im Rohtext
    genannten Instrumente zu schliessen, nicht nur das erste."""
    matches = []
    for pattern, name in _INDEX_ALIASES:
        m = pattern.search(text)
        if m:
            matches.append((m.start(), name))
    matches.sort(key=lambda pair: pair[0])
    return [name for _, name in matches]


_CLOSE_EVERYTHING_RE = re.compile(
    r"\bCLOS(?:E|ED|ING)\s+(ALL|EVERYTHING|BOTH)\b",
    re.IGNORECASE,
)


def closes_everything(text: str) -> bool:
    """True bei Schliess-Nachrichten, die sich auf ALLE aktuell offenen
    Positionen beziehen statt auf ein einzelnes Instrument (z. B. "CLOSED
    ALL", "CLOSED EVERYTHING", "closing both trades now" - alle drei live
    in genau diesem Kanal beobachtet).

    Sowohl _fast_parse als auch Gemini liefern pro Nachricht bewusst nur
    EIN "index"-Feld (siehe SYSTEM_PROMPT) - bei so einer Nachricht mit
    mehreren gleichzeitig offenen Instrumenten wurde dadurch bisher nur
    EINES geschlossen, das/die andere(n) blieben faelschlich offen. Live
    beobachtet (04.09.2026): "CLOSED ALL" schloss beide DAX-Teilpositionen
    (per mentioned_indices() bereits korrekt erkannt, da "DAX" im
    umgebenden Kontext genannt war), liess aber zwei echte UK100-
    Positionen unbemerkt offen, weil "ALL" selbst keinen Instrumentnamen
    enthaelt und mentioned_indices() dafuer leer bleibt.

    scripts/run_signal_bot.py nutzt dies zusaetzlich zu mentioned_indices(),
    um bei Treffer ALLE aktuell getrackten offenen Positionen zu
    schliessen, unabhaengig vom Instrument."""
    return bool(_CLOSE_EVERYTHING_RE.search(text))


def _parse_level(raw: str) -> float | None:
    raw = raw.replace(",", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _fast_parse(text: str) -> dict | None:
    """Liefert dasselbe dict-Format wie Gemini, oder None (dann uebernimmt
    parse_signal_message() wie gewohnt den Gemini-Aufruf)."""
    index = _single_index(text)
    if index is None:
        return None

    open_match = _OPEN_RE.search(text)
    close_match = _CLOSE_RE.search(text)

    if open_match and close_match:
        return None  # widerspruechlich - lieber Gemini entscheiden lassen statt zu raten

    if open_match:
        direction = "long" if open_match.group(1).upper().startswith("BOUGHT") else "short"
        return {
            "is_signal": True,
            "action": "open",
            "index": index,
            "direction": direction,
            "entry_level": _parse_level(open_match.group(2)),
            "stop_level": _parse_level(open_match.group(3)),
            "target_level": None,
        }

    if close_match:
        return {
            "is_signal": True,
            "action": "close",
            "index": index,
            "direction": None,
            "entry_level": None,
            "stop_level": None,
            "target_level": None,
        }

    return None


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
    scripts/run_signal_bot.py, signalbot/channel_log.py).

    Versucht ZUERST die regelbasierte Schnellerkennung (_fast_parse, siehe
    dort) - liefert die ein Ergebnis, entfaellt der Gemini-Aufruf
    komplett (Sekunden statt oft zehn-plus Sekunden, kein Timeout-Risiko).
    Nur bei einem unklaren Fall (None von _fast_parse) wird wie bisher
    Gemini gefragt."""
    if not text.strip():
        return None

    fast = _fast_parse(text)
    if fast is not None:
        print(f"[SignalBot] Schnellerkennung (ohne Gemini-Aufruf): action={fast['action']} index={fast['index']}")
        return fast

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
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

    last_error: Exception | None = None
    for attempt in range(1, GEMINI_RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(GEMINI_URL, params={"key": api_key}, json=payload, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            text_out = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text_out)
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as e:
            last_error = e
            print(f"[SignalBot] Gemini-Auswertung {attempt}/{GEMINI_RETRY_ATTEMPTS} fehlgeschlagen: {e}")
            if attempt < GEMINI_RETRY_ATTEMPTS:
                time.sleep(GEMINI_RETRY_DELAY_SECONDS)
    raise GeminiError(str(last_error)) from last_error
