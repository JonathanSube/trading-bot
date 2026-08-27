"""Hauptskript: Telegram-Signal-Bot. Ausgeloest per eigenem Workflow
(.github/workflows/signal-bot.yml), komplett getrennt vom ORB-Bot
(scripts/run_bot.py) - eigener Zustand (signal_state.json), eigenes
Protokoll (signal_trades.csv). Teilt sich mit dem ORB-Bot nur die
Kill-Switch-Datei (STOP).

Liest neue Nachrichten aus dem externen Signal-Kanal (KaraokeAndi, Live
Day Trading), laesst sie per LLM (Gemini) auswerten, uebersetzt
NASDAQ-/DOW-/DAX-/FTSE-Index-Signale auf echte OANDA-CFD-Instrumente
(signalbot/mapping.py) und fuehrt automatisch aus - ohne Rueckfrage, auf
ausdruecklichen Wunsch (siehe trading-bot-spec.md, Aenderungsprotokoll:
"Telegram-Signal-Ausfuehrung"). Laeuft auf einem OANDA-Practice-Konto
(nicht mehr Alpaca - siehe Aenderungsprotokoll zum Umstieg von
ETF-Proxys auf echte Index-CFDs). Der ORB-Bot bleibt unveraendert auf
Alpaca.

Ablauf pro Lauf:
1. Kill-Switch pruefen
2. Zustand laden, Konto abfragen
3. Offene Trades abgleichen (Fill nachtragen, geschlossene protokollieren)
4. Sicherheitsschalter pruefen (Gesamtverlust, API-Fehler)
5. Sessionende je Instrument (5 Min. vorher): betroffene offene Position
   zwangsschliessen - jedes Instrument hat seine eigene Handelszeit
   (US/London/Xetra), kein einzelner globaler EOD-Zeitpunkt mehr
6. Neue Kanal-Nachrichten holen, per LLM auswerten, ggf. Order platzieren
7. Zustand speichern
"""

import asyncio
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

from signalbot.mapping import build_signal_from_parsed, symbol_for_index
from signalbot.parser import parse_signal_message
from signalbot.state import OpenSignalTrade, SignalBotState, load_state, save_state
from signalbot.telegram_signals import fetch_new_messages
from signalbot.trade_log import SignalTradeLogRow, append_trade
from tradingbot.notify import send_notification
from tradingbot.oanda import (
    close_trade,
    get_account_summary,
    get_closed_trade,
    get_latest_price,
    get_open_trades,
    place_bracket_order,
    position_size,
)
from tradingbot.safety import check_kill_switch
from tradingbot.setup_detection import Direction

NY = ZoneInfo("America/New_York")
LONDON_TZ = ZoneInfo("Europe/London")
EU_TZ = ZoneInfo("Europe/Berlin")
STATE_PATH = ROOT / "signal_state.json"
KILL_SWITCH_PATH = ROOT / "STOP"
TRADE_LOG_PATH = ROOT / "signal_trades.csv"
CHANNEL = os.environ.get("SIGNAL_CHANNEL", "")
TOTAL_LOSS_LIMIT = -0.15
API_ERROR_LIMIT = 5

# Handelszeiten je OANDA-Instrument (Zeitzone, Sessionbeginn lokal,
# Sessionende lokal) - DST-sicher per ZoneInfo statt fixer UTC-Stunden,
# sonst verschiebt sich die Grenze mit Sommer-/Winterzeit. Kein
# Feiertagskalender (dafuer gibt es bei OANDA fuer Indizes keine
# aequivalente Quelle wie Alpacas Marktuhr fuer US-Aktien) - unschaedlich,
# fuehrt hoechstens zu ein paar ungenutzten Kanal-Abfragen an einem
# Feiertag. NAS100_USD/US30_USD: NYSE-Handelszeit (Kassamarkt). UK100_GBP:
# London-Session. DE30_EUR: Xetra-Session.
SESSIONS: dict[str, tuple[ZoneInfo, time, time]] = {
    "NAS100_USD": (NY, time(9, 30), time(16, 0)),
    "US30_USD": (NY, time(9, 30), time(16, 0)),
    "UK100_GBP": (LONDON_TZ, time(8, 0), time(16, 30)),
    "DE30_EUR": (EU_TZ, time(9, 0), time(17, 30)),
}
PRE_SESSION_LEAD_MINUTES = 5
# Rund um den Sessionstart kommt erfahrungsgemaess zuerst eine laengere
# Ansage-Nachricht im Kanal (Nutzerwunsch 27.08.2026) - deshalb wird die
# Ruhe-Drosselung unten in diesem Fenster (Vorlauf bis
# ACTIVE_POLLING_WINDOW_MINUTES nach Sessionstart) komplett ignoriert und
# jeder Lauf fragt den Kanal ab; erst danach greift die normale
# Ruhe-Drosselung (30 Min. still -> alle 5 Min.).
ACTIVE_POLLING_WINDOW_MINUTES = 60
QUIET_THRESHOLD_MINUTES = 30  # ab hier gilt der Kanal als "ruhig"
QUIET_POLL_INTERVAL_MINUTES = 5  # und wird nur noch in diesem Abstand abgefragt
# Risiko pro Trade fuer position_size() (tradingbot/oanda.py) - bewusst
# hoeher als der ORB-Bot-Standard von 1% (Nutzerwunsch 27.08.2026: die
# bisherigen Positionen waren angesichts der beobachteten Kursausschlaege
# zu klein, nur der Signal-Bot soll aggressiver dimensionieren, der
# ORB-Bot bleibt unangetastet bei 1%).
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
                   "eod": "Sessionende-Schluss", "safety_stop": "Sicherheitsschalter-Schluss"}
    send_notification(
        f"Signal-Trade geschlossen: {signal.direction.value.upper()} {symbol}, "
        f"{exit_labels.get(exit_reason, exit_reason)}\n"
        f"Ergebnis: {'+' if pnl >= 0 else ''}{pnl:.2f} ({row.pnl_in_r:+.2f} R)"
    )


def _check_filled_trades(account_id: str, state: SignalBotState, now: datetime) -> None:
    """OANDA fuellt Market-Orders synchron (kein Filled-Polling wie bei
    Alpaca noetig), aber die Bracket-Legs (Stop/Ziel) laufen serverseitig
    weiter - ein Trade, der bei OANDA nicht mehr unter den offenen Trades
    auftaucht, wurde durch Stop oder Ziel geschlossen."""
    open_at_broker = get_open_trades(account_id)
    for symbol in list(state.open_trades.keys()):
        trade = state.open_trades[symbol]

        if symbol in open_at_broker:
            trade.entry_fill = float(open_at_broker[symbol]["price"])
            continue

        try:
            closed = get_closed_trade(account_id, trade.order_id)
            exit_price = float(closed["averageClosePrice"])
        except Exception as e:
            print(f"Konnte geschlossenen Trade fuer {symbol} nicht abfragen: {e}")
            continue

        signal = trade.signal
        is_stop = abs(exit_price - signal.stop) < abs(exit_price - signal.target)
        _log_and_clear(state, symbol, now, exit_price, "stop" if is_stop else "target")


def _close_one_open(account_id: str, state: SignalBotState, symbol: str,
                     now: datetime, reason: str) -> None:
    trade = state.open_trades[symbol]
    try:
        close_trade(account_id, trade.order_id)
        closed = get_closed_trade(account_id, trade.order_id)
        exit_price = float(closed["averageClosePrice"])
    except Exception as e:
        print(f"Konnte Trade fuer {symbol} nicht schliessen: {e}")
        exit_price = trade.signal.entry_price
    _log_and_clear(state, symbol, now, exit_price, reason)


def _close_all_open(account_id: str, state: SignalBotState, now: datetime, reason: str) -> None:
    for symbol in list(state.open_trades.keys()):
        _close_one_open(account_id, state, symbol, now, reason)


def _close_expiring_positions(account_id: str, state: SignalBotState,
                               now: datetime, now_utc: datetime) -> None:
    """Zwangsschluss 5 Min. vor Sessionende - pro Instrument statt eines
    einzelnen globalen EOD-Zeitpunkts, da NAS100/US30/UK100/DE30 jeweils
    eigene Handelszeiten haben (siehe SESSIONS oben)."""
    for symbol in list(state.open_trades.keys()):
        if _session_end_approaching(now_utc, symbol):
            _close_one_open(account_id, state, symbol, now, "eod")


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
    Nachlauf danach (Nutzerwunsch 27.08.2026: "nach der Session gar nicht
    mehr abfragen bis 5 Min. vorher")."""
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
    """Drosselung auf Nutzerwunsch (26.08.2026): solange der Kanal aktiv
    ist, bei jedem Lauf abfragen (Takt macht der externe Trigger, siehe
    trading-bot-spec.md); nach QUIET_THRESHOLD_MINUTES ohne neue Nachricht
    nur noch alle QUIET_POLL_INTERVAL_MINUTES tatsaechlich abfragen. Der
    Cron-Trigger selbst kann diese Drosselung nicht - deshalb hier im
    Skript, nicht in der Cron-Konfiguration.

    Ausnahme (Nutzerwunsch 27.08.2026): rund um den Sessionstart kommt
    zuerst zuverlaessig eine laengere Ansage-Nachricht im Kanal - deshalb
    wird die Ruhe-Drosselung im aktiven Polling-Fenster
    (_any_active_polling_window) komplett ausgesetzt, unabhaengig davon
    wie lange der Kanal vorher still war. Erst danach greift die normale
    Drosselung wieder."""
    if _any_active_polling_window(now_utc):
        return True
    if state.last_channel_message_at is None or state.last_poll_at is None:
        return True
    quiet_minutes = (now_utc - state.last_channel_message_at).total_seconds() / 60
    if quiet_minutes < QUIET_THRESHOLD_MINUTES:
        return True
    since_last_poll = (now_utc - state.last_poll_at).total_seconds() / 60
    return since_last_poll >= QUIET_POLL_INTERVAL_MINUTES


def _try_new_signals(account_id: str, state: SignalBotState, equity: float, now_utc: datetime) -> None:
    if not CHANNEL:
        print("SIGNAL_CHANNEL nicht gesetzt, ueberspringe Kanal-Abruf.")
        return

    is_first_run = state.last_message_id is None

    try:
        messages = asyncio.run(fetch_new_messages(CHANNEL, state.last_message_id))
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
    # aus dem Verlauf als jetzige Einstiege interpretieren (live beobachtet
    # 26.08.2026: ~35 Alt-Nachrichten wurden beim ersten Lauf sofort an
    # Gemini geschickt, siehe auch das Ratenlimit-Problem unten).
    if is_first_run:
        if messages:
            state.last_message_id = messages[-1][0]
            print(f"Erster Lauf: {len(messages)} vorhandene Kanal-Nachrichten uebersprungen "
                  f"(Basislinie gesetzt), reagiere erst auf neue Nachrichten.")
        return

    if not messages:
        print("Kanal abgefragt, keine neuen Nachrichten seit dem letzten Lauf.")
        return

    for i, (message_id, text, _msg_date) in enumerate(messages):
        state.last_message_id = message_id

        if i > 0:
            time_module.sleep(GEMINI_CALL_DELAY_SECONDS)
        parsed = parse_signal_message(text)
        if parsed is None or not parsed.get("is_signal"):
            continue

        # Rohdaten mitloggen (Nutzerwunsch 27.08.2026, nach einer Nachfrage
        # zu Index-Punkten aus dem Kanal, die sich ohne Originaltext und
        # geparste Level nicht nachrechnen liess) - damit sich die spaeter
        # berechneten Werte (Entry/Stop/Ziel) jederzeit gegen die
        # tatsaechliche Kanal-Nachricht nachvollziehen lassen, auch wenn
        # DEFAULT_STOP_PCT als Fallback gegriffen hat (kein Stop-Level in
        # der Nachricht erkannt).
        print(f"Signal erkannt (Nachricht {message_id}): {parsed.get('index')} "
              f"{parsed.get('direction')}, Index-Level Entry={parsed.get('entry_level')} "
              f"Stop={parsed.get('stop_level')} Ziel={parsed.get('target_level')} | "
              f"Originaltext: {text!r}")

        symbol = symbol_for_index(parsed.get("index"))
        if symbol is None:
            print(f"Signal fuer Index '{parsed.get('index')}' erkannt, aber nicht unterstuetzt "
                  f"(nur NASDAQ/DOW/DAX/FTSE werden gehandelt) - uebersprungen.")
            continue
        if symbol in state.open_trades:
            print(f"Signal fuer {symbol}, aber bereits eine offene Position - uebersprungen.")
            continue

        try:
            instrument_price = get_latest_price(account_id, symbol)
        except Exception as e:
            print(f"Konnte aktuellen Kurs fuer {symbol} nicht laden: {e}")
            continue

        signal = build_signal_from_parsed(parsed, instrument_price, datetime.now(NY))
        if signal is None:
            continue

        units = position_size(signal, equity, risk_pct=SIGNAL_RISK_PCT)
        if units < 1:
            print(f"Signal fuer {symbol} erkannt, aber Stueckzahl < 1 - ausgelassen.")
            continue

        try:
            trade_id = place_bracket_order(account_id, symbol, signal, units)
        except Exception as e:
            # OANDA lehnt z. B. bei zu wenig Margin die Order direkt ab -
            # wie jeden anderen API-Fehler behandeln statt den ganzen Lauf
            # abzubrechen (siehe tradingbot/oanda.py::position_size).
            state.consecutive_api_errors += 1
            print(f"Order fuer {symbol} fehlgeschlagen: {e}")
            continue

        state.open_trades[symbol] = OpenSignalTrade(
            signal=signal, order_id=trade_id, qty=units, source_message_id=message_id,
        )
        send_notification(
            f"Signal-Einstieg {signal.direction.value} {units}x {symbol} @ ~{signal.entry_price:.2f} "
            f"(Kanal-Signal), Stop {signal.stop:.2f}, Ziel {signal.target:.2f}"
        )


def main() -> None:
    now = datetime.now(NY)

    kill_switch = check_kill_switch(KILL_SWITCH_PATH)
    if kill_switch is not None:
        print(f"Kill-Switch aktiv: {kill_switch.reason}. Beende ohne weitere Aktion.")
        return

    state = load_state(STATE_PATH)
    account_id = os.environ["OANDA_ACCOUNT_ID"]

    try:
        _run(account_id, state, now)
    except Exception as e:
        print(f"Unerwarteter Fehler: {e}")
        send_notification(f"Signal-Bot-Fehler: {e}")
        save_state(state, STATE_PATH)
        raise


def _run(account_id: str, state: SignalBotState, now: datetime) -> None:
    try:
        summary = get_account_summary(account_id)
        state.consecutive_api_errors = 0
    except Exception as e:
        state.consecutive_api_errors += 1
        print(f"API-Fehler beim Kontoabruf: {e}")
        save_state(state, STATE_PATH)
        return

    equity = float(summary["NAV"])
    if state.initial_equity is None:
        state.initial_equity = equity

    _check_filled_trades(account_id, state, now)

    if state.stopped_permanently:
        save_state(state, STATE_PATH)
        return

    total_loss = (equity - state.initial_equity) / state.initial_equity if state.initial_equity else 0.0
    if total_loss <= TOTAL_LOSS_LIMIT:
        print(f"Sicherheitsschalter: Gesamtverlust {total_loss * 100:.1f}%")
        send_notification(f"Signal-Bot Sicherheitsschalter: Gesamtverlust {total_loss * 100:.1f}%, stoppe dauerhaft.")
        _close_all_open(account_id, state, now, "safety_stop")
        state.stopped_permanently = True
        save_state(state, STATE_PATH)
        return

    if state.consecutive_api_errors >= API_ERROR_LIMIT:
        print(f"Sicherheitsschalter: {state.consecutive_api_errors} API-Fehler in Folge, keine neuen Einstiege.")
        save_state(state, STATE_PATH)
        return

    now_utc = datetime.now(timezone.utc)
    _close_expiring_positions(account_id, state, now, now_utc)

    market_window_open = _any_session_active(now_utc)
    if market_window_open and _should_poll_channel(state, now_utc):
        _try_new_signals(account_id, state, equity, now_utc)
    else:
        reason = "ausserhalb aller Handelsfenster" if not market_window_open else "Ruhe-Drosselung aktiv"
        print(f"Kein Kanal-Abruf diesen Lauf ({reason}).")
    save_state(state, STATE_PATH)


if __name__ == "__main__":
    main()
