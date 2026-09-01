"""Hauptskript: Telegram-Signal-Bot. Ausgeloest per eigenem Workflow
(.github/workflows/signal-bot.yml), komplett getrennt vom ORB-Bot
(scripts/run_bot.py) - eigener Zustand (signal_state.json), eigenes
Protokoll (signal_trades.csv). Teilt sich mit dem ORB-Bot nur die
Kill-Switch-Datei (STOP).

Liest neue Nachrichten aus dem externen Signal-Kanal (KaraokeAndi, Live
Day Trading), laesst sie per LLM (Gemini) auswerten, uebersetzt
NASDAQ-/DOW-/DAX-/FTSE-Index-Signale auf echte Pepperstone-Symbole
(signalbot/mapping.py, ueber die cTrader Open API) und fuehrt automatisch
aus - ohne Rueckfrage, auf ausdruecklichen Wunsch (siehe
trading-bot-spec.md, Aenderungsprotokoll: "Telegram-Signal-Ausfuehrung").
Laeuft auf einem Pepperstone-Demokonto (nicht Alpaca, urspruenglich auch
nicht OANDA/IG/MetaApi - siehe Aenderungsprotokoll zum mehrfachen
Broker-Wechsel). Der ORB-Bot bleibt unveraendert auf Alpaca.

Das gesamte Skript laeuft als eine `async def main()` statt der frueheren
synchronen Struktur - die cTrader Open API ist ein Twisted/Protobuf-
Protokoll ueber eine dauerhafte TCP-Verbindung, keine einfachen
REST-Aufrufe (siehe tradingbot/ctrader.py). Der bestehende Telegram-Abruf
(zuvor per eigenem `asyncio.run(...)`) laeuft jetzt im selben Event-Loop
mit.

Ablauf pro Lauf:
1. Kill-Switch pruefen
2. Zustand laden, cTrader-Session oeffnen (Access-Token holen, verbinden,
   Demo-Konto automatisch ermitteln - siehe tradingbot/ctrader.py)
3. Eingehende Telegram-Befehle beantworten (/status, /pause, /resume,
   /help - siehe signalbot/reporting.py; bewusst der einzige Bot der
   beiden, der das noch tut, siehe scripts/run_bot.py)
4. Konto abfragen
5. Offene Trades abgleichen (Fill nachtragen, geschlossene protokollieren)
6. Quellnachrichten offener Trades auf nachtraegliche Bearbeitung pruefen
   (Kanal ergaenzt Stop/Ziel oft erst per Edit nach dem Einstieg) - nur
   Stop/Ziel der bestehenden Position aktualisieren, NIE neu kaufen
   (siehe _check_message_edits)
7. Sicherheitsschalter pruefen (Gesamtverlust, API-Fehler)
8. Sessionende je Instrument (5 Min. vorher): betroffene offene Position
   zwangsschliessen - jedes Instrument hat seine eigene Handelszeit
   (US/London/Xetra), kein einzelner globaler EOD-Zeitpunkt
9. Pausiert per /pause? Dann keine neuen Einstiege, offene Positionen
   laufen trotzdem normal weiter (Stop/Ziel/Sessionende oben)
10. Neue Kanal-Nachrichten holen, per LLM auswerten, ggf. Order platzieren
11. Zustand speichern
"""

import os
import sys
import time as time_module
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from signalbot.channel_log import append_channel_message, recent_message_texts
from signalbot.mapping import INDEX_TO_SYMBOL, build_signal_from_parsed, symbol_for_index
from signalbot.parser import GeminiError, parse_signal_message
from signalbot.reporting import build_signal_status_report
from signalbot.state import OpenSignalTrade, SignalBotState, load_state, save_state
from signalbot.telegram_signals import fetch_messages_by_id, fetch_new_messages
from signalbot.trade_log import SignalTradeLogRow, append_trade
from tradingbot.ctrader import (
    CTraderSession,
    amend_position_sltp,
    close_position,
    ctrader_session,
    get_account_info,
    get_latest_price,
    get_open_positions,
    place_market_order,
    position_size,
    run_ctrader,
)
from tradingbot.notify import get_telegram_commands, send_notification
from tradingbot.safety import check_kill_switch
from tradingbot.setup_detection import Direction

NY = ZoneInfo("America/New_York")
LONDON_TZ = ZoneInfo("Europe/London")
EU_TZ = ZoneInfo("Europe/Berlin")
STATE_PATH = ROOT / "signal_state.json"
KILL_SWITCH_PATH = ROOT / "STOP"
TRADE_LOG_PATH = ROOT / "signal_trades.csv"
CHANNEL_LOG_PATH = ROOT / "signal_channel_log.csv"
CHANNEL = os.environ.get("SIGNAL_CHANNEL", "")
TOTAL_LOSS_LIMIT = -0.15
API_ERROR_LIMIT = 5

# Handelszeiten je Instrument (Zeitzone, Sessionbeginn lokal, Sessionende
# lokal) - DST-sicher per ZoneInfo statt fixer UTC-Stunden, sonst
# verschiebt sich die Grenze mit Sommer-/Winterzeit. Kein Feiertagskalender
# (dafuer gibt es keine aequivalente Quelle wie Alpacas Marktuhr fuer
# US-Aktien) - unschaedlich, fuehrt hoechstens zu ein paar ungenutzten
# Kanal-Abfragen an einem Feiertag. Ueber INDEX_TO_SYMBOL referenziert
# statt Symbole hier zu duplizieren, damit eine Korrektur der Symbole in
# signalbot/mapping.py (siehe dortiger Verifikations-Hinweis) automatisch
# mitzieht.
SESSIONS: dict[str, tuple[ZoneInfo, time, time]] = {
    INDEX_TO_SYMBOL["NASDAQ"]: (NY, time(9, 30), time(16, 0)),
    INDEX_TO_SYMBOL["DOW"]: (NY, time(9, 30), time(16, 0)),
    INDEX_TO_SYMBOL["FTSE"]: (LONDON_TZ, time(8, 0), time(16, 30)),
    INDEX_TO_SYMBOL["DAX"]: (EU_TZ, time(9, 0), time(17, 30)),
}
PRE_SESSION_LEAD_MINUTES = 5
# Rund um den Sessionstart kommt erfahrungsgemaess zuerst eine laengere
# Ansage-Nachricht im Kanal (Nutzerwunsch) - deshalb wird die
# Ruhe-Drosselung unten in diesem Fenster (Vorlauf bis
# ACTIVE_POLLING_WINDOW_MINUTES nach Sessionstart) komplett ignoriert und
# jeder Lauf fragt den Kanal ab; erst danach greift die normale
# Ruhe-Drosselung (30 Min. still -> alle 5 Min.).
ACTIVE_POLLING_WINDOW_MINUTES = 60
QUIET_THRESHOLD_MINUTES = 30  # ab hier gilt der Kanal als "ruhig"
QUIET_POLL_INTERVAL_MINUTES = 5  # und wird nur noch in diesem Abstand abgefragt
# Risiko pro Trade fuer position_size() (tradingbot/ctrader.py) - bewusst
# hoeher als der ORB-Bot-Standard von 1% (Nutzerwunsch: die bisherigen
# Positionen waren angesichts der beobachteten Kursausschlaege zu klein,
# nur der Signal-Bot soll aggressiver dimensionieren, der ORB-Bot bleibt
# unangetastet bei 1%).
SIGNAL_RISK_PCT = 0.03


def _log_and_clear(state: SignalBotState, symbol: str, now: datetime,
                    exit_price: float, exit_reason: str) -> None:
    trade = state.open_trades.pop(symbol)
    signal = trade.signal
    entry_actual = trade.entry_fill if trade.entry_fill is not None else signal.entry_price
    direction_mult = 1 if signal.direction is Direction.LONG else -1
    pnl = direction_mult * (exit_price - entry_actual) * trade.qty
    duration_minutes = (now - signal.entry_timestamp).total_seconds() / 60

    row = SignalTradeLogRow(
        timestamp=signal.entry_timestamp,
        symbol=symbol,
        direction=signal.direction,
        source_message_id=trade.source_message_id,
        entry_planned=signal.entry_price,
        entry_actual=entry_actual,
        stop=signal.stop,
        target=signal.target,
        qty=trade.qty,
        risk=signal.risk,
        exit_reason=exit_reason,
        exit_price=exit_price,
        pnl=pnl,
        duration_minutes=duration_minutes,
    )
    append_trade(TRADE_LOG_PATH, row)
    state.total_pnl += pnl
    state.total_trades += 1

    exit_labels = {"stop": "Stop getroffen", "target": "Ziel getroffen",
                   "eod": "Sessionende-Schluss", "safety_stop": "Sicherheitsschalter-Schluss",
                   "channel_close_signal": "Kanal-Schliess-Anweisung befolgt"}
    send_notification(
        f"Signal-Trade geschlossen: {signal.direction.value.upper()} {symbol}, "
        f"{exit_labels.get(exit_reason, exit_reason)}\n"
        f"Ergebnis: {'+' if pnl >= 0 else ''}{pnl:.2f} ({row.pnl_in_r:+.2f} R)"
    )


async def _check_filled_trades(session: CTraderSession, state: SignalBotState, now: datetime) -> None:
    """cTrader fuellt Market-Orders synchron (kein Filled-Polling wie bei
    Alpaca noetig), aber Stop/Ziel laufen serverseitig weiter - ein
    Trade, der nicht mehr unter den offenen Positionen auftaucht, wurde
    durch Stop oder Ziel geschlossen."""
    open_at_broker = await get_open_positions(session)
    for symbol in list(state.open_trades.keys()):
        trade = state.open_trades[symbol]

        if symbol in open_at_broker:
            trade.entry_fill = float(open_at_broker[symbol]["entryPrice"])
            continue

        # cTrader liefert (anders als IG/MetaApi) hier keinen einfachen
        # "Schlusskurs der zuletzt geschlossenen Position"-Aufruf, ohne die
        # noch unverifizierte Deal-Historie separat abzufragen - solange
        # das nicht gegen den echten Account geprueft ist (siehe
        # tradingbot/ctrader.py), wird der aktuelle Marktkurs als Naeherung
        # fuer den Ausstiegspreis verwendet (kein Datenverlust, nur eine
        # leicht ungenaue PnL-Zahl bis zur Verifikation) - naeher an Stop
        # oder Ziel entscheidet wie gewohnt den Log-Grund.
        signal = trade.signal
        try:
            exit_price = await get_latest_price(session, symbol)
        except Exception:
            exit_price = signal.stop
        print(f"Trade fuer {symbol} nicht mehr offen - genauer Ausstiegspreis noch nicht "
              f"verifiziert abrufbar (siehe tradingbot/ctrader.py), verwende aktuellen Marktkurs als Naeherung.")
        is_stop = abs(exit_price - signal.stop) < abs(exit_price - signal.target)
        _log_and_clear(state, symbol, now, exit_price, "stop" if is_stop else "target")


async def _check_message_edits(session: CTraderSession, state: SignalBotState, now_utc: datetime) -> None:
    """Nutzerwunsch (01.09.2026): der Kanal postet einen Einstieg oft zuerst
    OHNE Stop/Ziel und ergaenzt sie Sekunden spaeter per Bearbeitung
    derselben Nachricht - fetch_new_messages() sieht das nicht (nur neue
    message_id, keine Edits an bereits gesehenen Nachrichten). Hier werden
    GEZIELT nur die Quellnachrichten aktuell offener Trades erneut
    abgefragt (kein Scan der ganzen Historie); nur bei tatsaechlich
    neuerem edit_date erneut per Gemini ausgewertet. Es wird NIE ein neuer
    Einstieg ausgeloest - ausschliesslich Stop/Ziel der bereits offenen
    Position per amend_position_sltp() aktualisiert, falls sich aus den
    jetzt bekannten Kanal-Levels ein anderer Stop-Abstand ergibt als beim
    urspruenglichen Einstieg (der zuvor auf DEFAULT_STOP_PCT zurueckfallen
    musste, siehe signalbot/mapping.py)."""
    if not CHANNEL or not state.open_trades:
        return

    message_ids = [trade.source_message_id for trade in state.open_trades.values()]
    try:
        edited = await fetch_messages_by_id(CHANNEL, message_ids)
    except Exception as e:
        print(f"Konnte Bearbeitungen offener Signal-Nachrichten nicht abrufen: {e}")
        return

    for symbol, trade in list(state.open_trades.items()):
        result = edited.get(trade.source_message_id)
        if result is None:
            continue
        text, edit_date = result
        if edit_date is None or edit_date == trade.last_seen_edit_date:
            continue
        trade.last_seen_edit_date = edit_date

        try:
            history = recent_message_texts(CHANNEL_LOG_PATH, before=now_utc)
            parsed = parse_signal_message(text, history=history)
        except GeminiError as e:
            print(f"Bearbeitete Nachricht {trade.source_message_id} ({symbol}) nicht auswertbar: {e}")
            continue

        if parsed is None or not parsed.get("is_signal") or parsed.get("action") != "open":
            continue
        if symbol_for_index(parsed.get("index")) != symbol:
            continue

        entry_price = trade.entry_fill if trade.entry_fill is not None else trade.signal.entry_price
        updated = build_signal_from_parsed(parsed, entry_price, trade.signal.entry_timestamp)
        if updated is None or abs(updated.stop - trade.signal.stop) < 0.01:
            continue

        try:
            await amend_position_sltp(session, trade.order_id, updated.stop, updated.target)
        except Exception as e:
            print(f"Konnte Stop/Ziel fuer {symbol} nach Nachrichten-Bearbeitung nicht aktualisieren: {e}")
            continue

        trade.signal = updated
        print(f"Stop/Ziel fuer {symbol} nach Bearbeitung der Kanal-Nachricht aktualisiert: "
              f"Stop {updated.stop:.2f}, Ziel {updated.target:.2f}")
        send_notification(
            f"Signal-Update {symbol}: Kanal hat die Einstiegsnachricht bearbeitet, "
            f"Stop/Ziel angepasst -> Stop {updated.stop:.2f}, Ziel {updated.target:.2f}"
        )
        append_channel_message(CHANNEL_LOG_PATH, now_utc, trade.source_message_id, text, parsed, "levels_aktualisiert")


async def _close_one_open(session: CTraderSession, state: SignalBotState, symbol: str,
                           now: datetime, reason: str) -> None:
    """Live beobachtet (01.09.2026): close_position() bekommt auf
    ProtoOAClosePositionReq nur die sofortige "Order angenommen"-Antwort
    zurueck (orderStatus ORDER_STATUS_ACCEPTED, executedVolume 0, KEIN
    deal.executionPrice) - der eigentliche Fill kommt als separates,
    asynchrones Server-Event nach, das aktuell nicht abgewartet wird, siehe
    tradingbot/ctrader.py::close_position. Bisher fiel der Ausstiegspreis
    in diesem (haeufigen, nicht dem seltenen) Fall auf den urspruenglichen
    Entry-Preis zurueck - das ergab IMMER exakt 0,00 PnL, obwohl die
    Position real zu einem anderen Kurs geschlossen wurde (Nutzer-Feedback
    01.09.2026: "die Trades standen alle null null, das kann nicht
    passen"). Fallback jetzt auf den aktuellen Marktkurs statt auf den
    Entry-Preis - kein exakter Fill, aber naeher an der Realitaet als ein
    garantiertes Nullergebnis (gleiches Verfahren wie in
    _check_filled_trades() fuer Stop-/Ziel-Schluesse ohne verifizierten
    Fill-Preis)."""
    trade = state.open_trades[symbol]
    try:
        exit_price = await close_position(session, trade.order_id, trade.qty)
    except Exception as e:
        print(f"Konnte Trade fuer {symbol} nicht schliessen (oder Ausstiegspreis nicht ermittelbar): {e}")
        try:
            exit_price = await get_latest_price(session, symbol)
        except Exception:
            exit_price = trade.signal.entry_price
    _log_and_clear(state, symbol, now, exit_price, reason)


async def _close_all_open(session: CTraderSession, state: SignalBotState, now: datetime, reason: str) -> None:
    for symbol in list(state.open_trades.keys()):
        await _close_one_open(session, state, symbol, now, reason)


async def _close_expiring_positions(session: CTraderSession, state: SignalBotState,
                                     now: datetime, now_utc: datetime) -> None:
    """Zwangsschluss 5 Min. vor Sessionende - pro Instrument statt eines
    einzelnen globalen EOD-Zeitpunkts, da NASDAQ/DOW/UK100/DAX jeweils
    eigene Handelszeiten haben (siehe SESSIONS oben)."""
    for symbol in list(state.open_trades.keys()):
        if _session_end_approaching(now_utc, symbol):
            await _close_one_open(session, state, symbol, now, "eod")


GEMINI_CALL_DELAY_SECONDS = 4  # Freikontingent-Ratenlimit, siehe trading-bot-spec.md Abschnitt 12


def _session_bounds(now_utc: datetime, symbol: str) -> tuple[datetime, datetime] | None:
    tz, start_local, end_local = SESSIONS[symbol]
    now_local = now_utc.astimezone(tz)
    if now_local.weekday() >= 5:
        return None
    session_start = datetime.combine(now_local.date(), start_local, tzinfo=tz)
    session_end = datetime.combine(now_local.date(), end_local, tzinfo=tz)
    return session_start, session_end


def _is_in_session(now_utc: datetime, symbol: str) -> bool:
    """PRE_SESSION_LEAD_MINUTES vor Sessionbeginn bis Sessionende, kein
    Nachlauf danach (Nutzerwunsch: "nach der Session gar nicht mehr
    abfragen bis 5 Min. vorher")."""
    bounds = _session_bounds(now_utc, symbol)
    if bounds is None:
        return False
    session_start, session_end = bounds
    now_local = now_utc.astimezone(SESSIONS[symbol][0])
    poll_start = session_start - timedelta(minutes=PRE_SESSION_LEAD_MINUTES)
    return poll_start <= now_local <= session_end


def _is_in_active_polling_window(now_utc: datetime, symbol: str) -> bool:
    """True von PRE_SESSION_LEAD_MINUTES vor bis ACTIVE_POLLING_WINDOW_MINUTES
    nach Sessionstart - in diesem Fenster ignoriert _should_poll_channel()
    die Ruhe-Drosselung komplett, siehe deren Docstring."""
    bounds = _session_bounds(now_utc, symbol)
    if bounds is None:
        return False
    session_start, _ = bounds
    now_local = now_utc.astimezone(SESSIONS[symbol][0])
    window_start = session_start - timedelta(minutes=PRE_SESSION_LEAD_MINUTES)
    window_end = session_start + timedelta(minutes=ACTIVE_POLLING_WINDOW_MINUTES)
    return window_start <= now_local <= window_end


def _session_end_approaching(now_utc: datetime, symbol: str) -> bool:
    """5 Min. vor Sessionende - Zeitpunkt fuer den Zwangsschluss einer
    offenen Position in diesem Instrument."""
    bounds = _session_bounds(now_utc, symbol)
    if bounds is None:
        return False
    _, session_end = bounds
    now_local = now_utc.astimezone(SESSIONS[symbol][0])
    return now_local >= session_end - timedelta(minutes=PRE_SESSION_LEAD_MINUTES)


def _any_session_active(now_utc: datetime) -> bool:
    return any(_is_in_session(now_utc, symbol) for symbol in SESSIONS)


def _any_active_polling_window(now_utc: datetime) -> bool:
    return any(_is_in_active_polling_window(now_utc, symbol) for symbol in SESSIONS)


def _should_poll_channel(state: SignalBotState, now_utc: datetime) -> bool:
    """Drosselung auf Nutzerwunsch: solange der Kanal aktiv ist, bei jedem
    Lauf abfragen (Takt macht der externe Trigger, siehe
    trading-bot-spec.md); nach QUIET_THRESHOLD_MINUTES ohne neue Nachricht
    nur noch alle QUIET_POLL_INTERVAL_MINUTES tatsaechlich abfragen. Der
    Cron-Trigger selbst kann diese Drosselung nicht - deshalb hier im
    Skript, nicht in der Cron-Konfiguration.

    Ausnahme (Nutzerwunsch): rund um den Sessionstart kommt zuerst
    zuverlaessig eine laengere Ansage-Nachricht im Kanal - deshalb wird die
    Ruhe-Drosselung im aktiven Polling-Fenster (_any_active_polling_window)
    komplett ausgesetzt, unabhaengig davon wie lange der Kanal vorher still
    war. Erst danach greift die normale Drosselung wieder."""
    if _any_active_polling_window(now_utc):
        return True
    if state.last_channel_message_at is None or state.last_poll_at is None:
        return True
    quiet_minutes = (now_utc - state.last_channel_message_at).total_seconds() / 60
    if quiet_minutes < QUIET_THRESHOLD_MINUTES:
        return True
    since_last_poll = (now_utc - state.last_poll_at).total_seconds() / 60
    return since_last_poll >= QUIET_POLL_INTERVAL_MINUTES


async def _try_new_signals(session: CTraderSession, state: SignalBotState,
                            equity: float, now_utc: datetime) -> None:
    if not CHANNEL:
        print("SIGNAL_CHANNEL nicht gesetzt, ueberspringe Kanal-Abruf.")
        return

    is_first_run = state.last_message_id is None

    try:
        messages = await fetch_new_messages(CHANNEL, state.last_message_id)
    except Exception as e:
        state.consecutive_api_errors += 1
        print(f"API-Fehler beim Telegram-Abruf: {e}")
        return

    state.last_poll_at = now_utc
    if messages:
        state.last_channel_message_at = messages[-1][2]

    # Allererster Lauf: nur die Basislinie setzen (last_message_id), keine
    # der schon vorhandenen Kanal-Nachrichten als aktuelles Signal handeln -
    # ohne das wuerde der Bot beim Start tagealte "BOUGHT LONG"-Nachrichten
    # aus dem Verlauf als jetzige Einstiege interpretieren (live beobachtet:
    # ~35 Alt-Nachrichten wurden beim ersten Lauf sofort an Gemini
    # geschickt, siehe auch das Ratenlimit-Problem unten).
    if is_first_run:
        if messages:
            state.last_message_id = messages[-1][0]
            print(f"Erster Lauf: {len(messages)} vorhandene Kanal-Nachrichten uebersprungen "
                  f"(Basislinie gesetzt), reagiere erst auf neue Nachrichten.")
        return

    if not messages:
        print("Kanal abgefragt, keine neuen Nachrichten seit dem letzten Lauf.")
        return

    for i, (message_id, text, msg_date) in enumerate(messages):
        state.last_message_id = message_id

        if i > 0:
            time_module.sleep(GEMINI_CALL_DELAY_SECONDS)

        # JEDE ausgewertete Nachricht wird protokolliert (nicht nur die,
        # die zu einem Trade fuehren) - Nutzerwunsch (28.08.2026): die
        # letzten sieben Tage sollen jederzeit nachvollziehbar sein, u. a.
        # um zu sehen, warum ein gesendetes Signal NICHT gehandelt wurde
        # (der Bot hatte zuvor nur 1 von ueber 5 gesendeten Signalen
        # beachtet, ohne dass der Grund dafuer sichtbar war).
        try:
            history = recent_message_texts(CHANNEL_LOG_PATH, before=msg_date)
            parsed = parse_signal_message(text, history=history)
        except GeminiError as e:
            state.consecutive_api_errors += 1
            print(f"Nachricht {message_id} uebersprungen (Gemini-Fehler): {e}")
            append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, None, "gemini_fehler")
            continue

        if parsed is None or not parsed.get("is_signal"):
            append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, parsed, "kein_signal")
            continue

        # Rohdaten mitloggen (Nutzerwunsch, nach einer Nachfrage zu
        # Index-Punkten aus dem Kanal, die sich ohne Originaltext und
        # geparste Level nicht nachrechnen liess) - damit sich die spaeter
        # berechneten Werte (Entry/Stop/Ziel) jederzeit gegen die
        # tatsaechliche Kanal-Nachricht nachvollziehen lassen, auch wenn
        # DEFAULT_STOP_PCT als Fallback gegriffen hat (kein Stop-Level in
        # der Nachricht erkannt).
        print(f"Signal erkannt (Nachricht {message_id}): action={parsed.get('action')} "
              f"{parsed.get('index')} {parsed.get('direction')}, Index-Level "
              f"Entry={parsed.get('entry_level')} Stop={parsed.get('stop_level')} "
              f"Ziel={parsed.get('target_level')} | Originaltext: {text!r}")

        symbol = symbol_for_index(parsed.get("index"))
        if symbol is None:
            print(f"Signal fuer Index '{parsed.get('index')}' erkannt, aber nicht unterstuetzt "
                  f"(nur NASDAQ/DOW/DAX/FTSE werden gehandelt) - uebersprungen.")
            append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, parsed, "index_nicht_unterstuetzt")
            continue

        if parsed.get("action") == "close":
            # Eindeutige Kanal-Anweisung, die laufende Position JETZT zu
            # schliessen (Nutzerwunsch 28.08.2026: der Bot hatte eine
            # solche Nachricht zuvor ignoriert und stattdessen passiv auf
            # den eigenen Stop gewartet) - nur wirksam, wenn ueberhaupt
            # eine offene Position in diesem Instrument besteht.
            if symbol in state.open_trades:
                await _close_one_open(session, state, symbol, datetime.now(NY), "channel_close_signal")
                append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, parsed, "trade_geschlossen")
            else:
                print(f"Schliess-Anweisung fuer {symbol}, aber keine offene Position - ignoriert.")
                append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, parsed, "schliessung_ohne_position")
            continue

        if symbol in state.open_trades:
            print(f"Signal fuer {symbol}, aber bereits eine offene Position - uebersprungen.")
            append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, parsed, "bereits_offen")
            continue

        try:
            instrument_price = await get_latest_price(session, symbol)
        except Exception as e:
            print(f"Konnte aktuellen Kurs fuer {symbol} nicht laden: {e}")
            append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, parsed, "kurs_nicht_ladbar")
            continue

        signal = build_signal_from_parsed(parsed, instrument_price, datetime.now(NY))
        if signal is None:
            append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, parsed, "kein_gueltiges_signal")
            continue

        volume = position_size(signal, equity, risk_pct=SIGNAL_RISK_PCT)
        if volume < 0.01:
            print(f"Signal fuer {symbol} erkannt, aber Lot-Volumen < 0.01 - ausgelassen.")
            append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, parsed, "volumen_zu_klein")
            continue

        try:
            position_id = await place_market_order(session, symbol, signal, volume)
        except Exception as e:
            # cTrader/der Broker lehnt z. B. bei zu wenig Margin oder
            # einem falschen Symbol die Order direkt ab - wie jeden
            # anderen API-Fehler behandeln statt den ganzen Lauf
            # abzubrechen (siehe tradingbot/ctrader.py::position_size zur
            # Margin-Begruendung).
            state.consecutive_api_errors += 1
            print(f"Order fuer {symbol} fehlgeschlagen: {e}")
            append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, parsed, "order_fehlgeschlagen")
            continue

        # Stop/Ziel an den TATSAECHLICHEN Fill-Kurs anpassen, nicht am vor
        # der Order abgefragten instrument_price haengen bleiben -
        # Nutzerwunsch (01.09.2026), nachdem eine grosse Slippage beim
        # US30-Einstieg (124 Punkte) den geplanten Risikopuffer bis zum
        # Stop fast komplett aufgezehrt hatte (Stop lag praktisch direkt am
        # Fill). Bewusst KEIN neu erfundener Stop-Abstand - dieselbe
        # prozentuale Distanz aus der Kanal-Nachricht (build_signal_from_
        # parsed), nur auf den echten Kaufkurs statt der Vorab-Schaetzung
        # angewendet. Weniger guenstiger Fill -> etwas weniger Gewinn bis
        # zum Ziel, aber der Stop-Abstand bleibt wie vom Kanal vorgegeben.
        entry_fill = None
        try:
            open_at_broker = await get_open_positions(session)
            entry_fill = open_at_broker.get(symbol, {}).get("entryPrice")
        except Exception as e:
            print(f"Konnte tatsaechlichen Fill-Kurs fuer {symbol} nicht abrufen: {e}")

        if entry_fill is not None and abs(entry_fill - signal.entry_price) > 0.01:
            corrected_signal = build_signal_from_parsed(parsed, entry_fill, signal.entry_timestamp)
            if corrected_signal is not None:
                try:
                    await amend_position_sltp(session, position_id, corrected_signal.stop, corrected_signal.target)
                    print(f"Stop/Ziel fuer {symbol} an tatsaechlichen Fill-Kurs {entry_fill:.2f} angepasst "
                          f"(statt Vorab-Schaetzung {signal.entry_price:.2f}): "
                          f"Stop {corrected_signal.stop:.2f}, Ziel {corrected_signal.target:.2f}.")
                    signal = corrected_signal
                except Exception as e:
                    print(f"Konnte Stop/Ziel fuer {symbol} nicht an den Fill-Kurs anpassen, "
                          f"bleibe bei der Vorab-Schaetzung: {e}")

        # last_seen_edit_date auf den JETZIGEN edit_date der Nachricht
        # setzen, nicht auf None lassen (Fehler, gefunden 01.09.2026 anhand
        # eines Nutzer-Berichts ueber ein unerklaertes Stop/Ziel-Update):
        # Telegram vergibt oft schon kurz nach dem Posten einen edit_date
        # (z.B. durch Linkvorschau-Generierung), auch ohne inhaltliche
        # Aenderung. Bliebe last_seen_edit_date bei None, wuerde
        # _check_message_edits() genau diesen harmlosen, schon beim
        # Einstieg vorhandenen edit_date beim naechsten Lauf faelschlich
        # als NEUE Bearbeitung werten und Stop/Ziel unnoetig neu berechnen -
        # verwirrend, wenn sich die Kanal-Nachricht gar nicht geaendert hat.
        try:
            baseline = await fetch_messages_by_id(CHANNEL, [message_id])
            initial_edit_date = baseline.get(message_id, (None, None))[1]
        except Exception:
            initial_edit_date = None

        state.open_trades[symbol] = OpenSignalTrade(
            signal=signal, order_id=position_id, qty=volume, source_message_id=message_id,
            entry_fill=entry_fill, last_seen_edit_date=initial_edit_date,
        )
        append_channel_message(CHANNEL_LOG_PATH, msg_date, message_id, text, parsed, "trade_eroeffnet")
        send_notification(
            f"Signal-Einstieg {signal.direction.value} {volume}x {symbol} @ ~{signal.entry_price:.2f} "
            f"(Kanal-Signal), Stop {signal.stop:.2f}, Ziel {signal.target:.2f}"
        )


async def main() -> None:
    now = datetime.now(NY)

    kill_switch = check_kill_switch(KILL_SWITCH_PATH)
    if kill_switch is not None:
        print(f"Kill-Switch aktiv: {kill_switch.reason}. Beende ohne weitere Aktion.")
        return

    state = load_state(STATE_PATH)

    try:
        async with ctrader_session() as session:
            await _run(session, state, now)
    except Exception as e:
        print(f"Unerwarteter Fehler: {e}")
        send_notification(f"Signal-Bot-Fehler: {e}")
        save_state(state, STATE_PATH)
        raise


async def handle_telegram_commands(session: CTraderSession, state: SignalBotState) -> None:
    """/status und /help per Telegram - bewusst nur hier, nicht mehr beim
    ORB-Bot (siehe scripts/run_bot.py::send_daily_report zur Begruendung):
    beide Bots teilen sich Bot-Token/Chat, Telegrams getUpdates-Offset ist
    global pro Bot-Token, zwei gleichzeitig pollende Bots wuerden sich die
    Befehls-Updates gegenseitig wegschnappen."""
    commands, new_offset = get_telegram_commands(state.telegram_update_offset)
    state.telegram_update_offset = new_offset

    for command in commands:
        cmd = command.split()[0].lower()
        if cmd == "/status":
            report = await build_signal_status_report(session, state, TRADE_LOG_PATH)
            send_notification(report)
        elif cmd == "/pause":
            state.paused = True
            send_notification("Signal-Bot pausiert: keine neuen Einstiege mehr, offene Positionen laufen normal weiter (Stop/Ziel/Sessionende). Mit /resume wieder freigeben.")
        elif cmd == "/resume":
            state.paused = False
            send_notification("Signal-Bot fortgesetzt: neue Einstiege wieder erlaubt.")
        elif cmd == "/help":
            send_notification(
                "Verfuegbare Befehle:\n"
                "/status - Kontostand, offene Positionen mit aktuellem Kurs/Abstand zu Stop&Ziel, letzte Trades\n"
                "/pause - keine neuen Einstiege mehr (offene Positionen bleiben unberuehrt)\n"
                "/resume - neue Einstiege wieder erlauben\n"
                "/help - diese Uebersicht"
            )


async def _run(session: CTraderSession, state: SignalBotState, now: datetime) -> None:
    await handle_telegram_commands(session, state)

    try:
        account = await get_account_info(session)
        state.consecutive_api_errors = 0
    except Exception as e:
        state.consecutive_api_errors += 1
        print(f"API-Fehler beim Kontoabruf: {e}")
        save_state(state, STATE_PATH)
        return

    equity = float(account["balance"])
    if state.initial_equity is None:
        state.initial_equity = equity

    await _check_filled_trades(session, state, now)
    await _check_message_edits(session, state, datetime.now(timezone.utc))

    if state.stopped_permanently:
        save_state(state, STATE_PATH)
        return

    total_loss = (equity - state.initial_equity) / state.initial_equity if state.initial_equity else 0.0
    if total_loss <= TOTAL_LOSS_LIMIT:
        print(f"Sicherheitsschalter: Gesamtverlust {total_loss * 100:.1f}%")
        send_notification(f"Signal-Bot Sicherheitsschalter: Gesamtverlust {total_loss * 100:.1f}%, stoppe dauerhaft.")
        await _close_all_open(session, state, now, "safety_stop")
        state.stopped_permanently = True
        save_state(state, STATE_PATH)
        return

    if state.consecutive_api_errors >= API_ERROR_LIMIT:
        print(f"Sicherheitsschalter: {state.consecutive_api_errors} API-Fehler in Folge, keine neuen Einstiege.")
        save_state(state, STATE_PATH)
        return

    now_utc = datetime.now(timezone.utc)
    await _close_expiring_positions(session, state, now, now_utc)

    if state.paused:
        print("Per /pause pausiert: keine neuen Einstiege diesen Lauf (offene Positionen laufen weiter).")
        save_state(state, STATE_PATH)
        return

    market_window_open = _any_session_active(now_utc)
    if market_window_open and _should_poll_channel(state, now_utc):
        await _try_new_signals(session, state, equity, now_utc)
    else:
        reason = "ausserhalb aller Handelsfenster" if not market_window_open else "Ruhe-Drosselung aktiv"
        print(f"Kein Kanal-Abruf diesen Lauf ({reason}).")
    save_state(state, STATE_PATH)


if __name__ == "__main__":
    # bewusst run_ctrader() statt asyncio.run() - siehe tradingbot/ctrader.py
    run_ctrader(main())
