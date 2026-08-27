"""Hauptskript: Telegram-Signal-Bot. Ausgeloest per eigenem Workflow
(.github/workflows/signal-bot.yml), komplett getrennt vom ORB-Bot
(scripts/run_bot.py) - eigener Zustand (signal_state.json), eigenes
Protokoll (signal_trades.csv). Teilt sich mit dem ORB-Bot nur die
Kill-Switch-Datei (STOP) und das Alpaca-Konto.

Liest neue Nachrichten aus dem externen Signal-Kanal (KaraokeAndi, Live
Day Trading), laesst sie per LLM (Gemini) auswerten, uebersetzt
NASDAQ-/DOW-Index-Signale auf QQQ/DIA (signalbot/mapping.py) und fuehrt
automatisch aus - ohne Rueckfrage, auf ausdruecklichen Wunsch (siehe
trading-bot-spec.md, Aenderungsprotokoll: "Telegram-Signal-Ausfuehrung").
Laeuft auf demselben Alpaca-Paper-Konto wie der ORB-Bot.

Ablauf pro Lauf:
1. Kill-Switch pruefen
2. Zustand laden, Konto abfragen
3. Offene Trades abgleichen (Fill nachtragen, geschlossene protokollieren)
4. Sicherheitsschalter pruefen (Gesamtverlust, API-Fehler)
5. Tagesende (15:55 ET): offene Positionen zwangsschliessen
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

from alpaca.trading.client import TradingClient

from signalbot.mapping import build_signal_from_parsed, symbol_for_index
from signalbot.parser import parse_signal_message
from signalbot.state import OpenSignalTrade, SignalBotState, load_state, save_state
from signalbot.telegram_signals import fetch_new_messages
from signalbot.trade_log import SignalTradeLogRow, append_trade
from tradingbot.data import get_latest_price
from tradingbot.notify import send_notification
from tradingbot.orders import place_bracket_order, position_size
from tradingbot.safety import check_kill_switch
from tradingbot.setup_detection import Direction

NY = ZoneInfo("America/New_York")
STATE_PATH = ROOT / "signal_state.json"
KILL_SWITCH_PATH = ROOT / "STOP"
TRADE_LOG_PATH = ROOT / "signal_trades.csv"
EOD_CUTOFF = time(15, 55)
CHANNEL = os.environ.get("SIGNAL_CHANNEL", "")
TOTAL_LOSS_LIMIT = -0.15
API_ERROR_LIMIT = 5

# EU-Handelsfenster: Start ist der tatsaechliche Xetra/Euronext-Handelsbeginn
# (09:00 Europe/Berlin, DST-sicher per ZoneInfo statt fixer UTC-Stunde -
# sonst verschiebt sich die Grenze mit Sommer-/Winterzeit) minus
# PRE_SESSION_LEAD_MINUTES Vorlauf, auf Nutzerwunsch (27.08.2026: "5 Minuten
# vor Beginn der Trading-Sessions minuetlich gucken"). Ende bleibt die
# bisherige grosszuegige UTC-Pauschale - der externe Cron-Trigger selbst
# deckt ein noch breiteres Fenster ab (siehe trading-bot-spec.md), das
# eigentliche Ein-/Ausschalten passiert hier im Skript, gleiches Prinzip wie
# beim ORB-Bot (Abschnitt 9, "Workflow bewusst weiter getaktet als die
# Session").
EU_TZ = ZoneInfo("Europe/Berlin")
EU_SESSION_START_LOCAL = time(9, 0)
EU_HOURS_END_UTC = time(17, 0)
# US-Handelsbeginn (NYSE/NASDAQ): 09:30 America/New_York, ebenfalls DST-sicher
# per ZoneInfo (NY-Konstante oben) statt fixer UTC-Stunde.
US_SESSION_START_LOCAL = time(9, 30)
PRE_SESSION_LEAD_MINUTES = 5
QUIET_THRESHOLD_MINUTES = 30  # ab hier gilt der Kanal als "ruhig"
QUIET_POLL_INTERVAL_MINUTES = 5  # und wird nur noch in diesem Abstand abgefragt


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
                   "eod": "Tagesende-Schluss", "safety_stop": "Sicherheitsschalter-Schluss"}
    send_notification(
        f"Signal-Trade geschlossen: {signal.direction.value.upper()} {symbol}, "
        f"{exit_labels.get(exit_reason, exit_reason)}\n"
        f"Ergebnis: {'+' if pnl >= 0 else ''}{pnl:.2f} $ ({row.pnl_in_r:+.2f} R)"
    )


def _check_filled_trades(client: TradingClient, state: SignalBotState, now: datetime) -> None:
    for symbol in list(state.open_trades.keys()):
        trade = state.open_trades[symbol]

        if trade.entry_fill is None:
            order = client.get_order_by_id(trade.order_id)
            if order.filled_avg_price is not None:
                trade.entry_fill = float(order.filled_avg_price)

        order = client.get_order_by_id(trade.order_id)
        filled_leg = next((leg for leg in (order.legs or []) if leg.status == "filled"), None)
        if filled_leg is None:
            continue

        exit_price = float(filled_leg.filled_avg_price)
        signal = trade.signal
        is_stop = abs(exit_price - signal.stop) < abs(exit_price - signal.target)
        _log_and_clear(state, symbol, now, exit_price, "stop" if is_stop else "target")


def _close_all_open(client: TradingClient, state: SignalBotState, now: datetime, reason: str) -> None:
    for symbol in list(state.open_trades.keys()):
        trade = state.open_trades[symbol]
        # Bracket-Legs zuerst canceln, sonst lehnt Alpaca die Schliess-Order
        # ab ("insufficient qty available") - siehe scripts/run_bot.py,
        # force_close_open_position, selbe Ursache live beobachtet am
        # 25./26.08.2026 beim ORB-Bot.
        try:
            order = client.get_order_by_id(trade.order_id)
            for leg in (order.legs or []):
                if leg.status not in ("filled", "canceled", "expired"):
                    client.cancel_order_by_id(leg.id)
        except Exception as e:
            print(f"Konnte Bracket-Legs fuer {symbol} nicht abfragen/canceln: {e}")

        positions = client.get_all_positions()
        if any(p.symbol == symbol for p in positions):
            order = client.close_position(symbol)
            exit_price = None
            for _ in range(3):
                if order.filled_avg_price is not None:
                    exit_price = float(order.filled_avg_price)
                    break
                time_module.sleep(2)
                order = client.get_order_by_id(order.id)
            if exit_price is None:
                exit_price = trade.signal.entry_price
        else:
            exit_price = trade.signal.stop

        _log_and_clear(state, symbol, now, exit_price, reason)


GEMINI_CALL_DELAY_SECONDS = 4  # Freikontingent-Ratenlimit, siehe trading-bot-spec.md Abschnitt 12


def _is_eu_hours(now_utc: datetime) -> bool:
    """Keine exakte Boersenkalender-Pruefung wie beim US-Markt (dafuer gibt
    es hier keine Alpaca-aequivalente Quelle) - Feiertage werden nicht
    beruecksichtigt, bewusst grosszuegiges Ende, siehe EU_HOURS_END_UTC
    oben. Start ist PRE_SESSION_LEAD_MINUTES vor EU_SESSION_START_LOCAL."""
    now_eu = now_utc.astimezone(EU_TZ)
    if now_eu.weekday() >= 5:
        return False
    session_start = datetime.combine(now_eu.date(), EU_SESSION_START_LOCAL, tzinfo=EU_TZ)
    poll_start = session_start - timedelta(minutes=PRE_SESSION_LEAD_MINUTES)
    return poll_start <= now_eu and now_utc.time() <= EU_HOURS_END_UTC


def _is_us_pre_session(now_utc: datetime) -> bool:
    """Alpacas Clock (_is_us_market_open) kennt nur den tatsaechlichen
    Marktstatus, keinen Vorlauf - deshalb hier separat: die
    PRE_SESSION_LEAD_MINUTES vor US_SESSION_START_LOCAL. Feiertage werden
    nicht beruecksichtigt (dafuer muesste die Alpaca-Clock/-Kalender befragt
    werden) - unschaedlich, fuehrt hoechstens zu ein paar ungenutzten
    Kanal-Abfragen an einem Boersenfeiertag."""
    now_ny = now_utc.astimezone(NY)
    if now_ny.weekday() >= 5:
        return False
    session_start = datetime.combine(now_ny.date(), US_SESSION_START_LOCAL, tzinfo=NY)
    poll_start = session_start - timedelta(minutes=PRE_SESSION_LEAD_MINUTES)
    return poll_start <= now_ny < session_start


def _is_us_market_open(client: TradingClient) -> bool:
    try:
        return bool(client.get_clock().is_open)
    except Exception as e:
        print(f"Konnte US-Marktkalender nicht abrufen, nehme Markt vorsichtshalber als offen an: {e}")
        return True


def _should_poll_channel(state: SignalBotState, now_utc: datetime) -> bool:
    """Drosselung auf Nutzerwunsch (26.08.2026): solange der Kanal aktiv
    ist, bei jedem Lauf abfragen (Takt macht der externe Trigger, siehe
    trading-bot-spec.md); nach QUIET_THRESHOLD_MINUTES ohne neue Nachricht
    nur noch alle QUIET_POLL_INTERVAL_MINUTES tatsaechlich abfragen. Der
    Cron-Trigger selbst kann diese Drosselung nicht - deshalb hier im
    Skript, nicht in der Cron-Konfiguration."""
    if state.last_channel_message_at is None or state.last_poll_at is None:
        return True
    quiet_minutes = (now_utc - state.last_channel_message_at).total_seconds() / 60
    if quiet_minutes < QUIET_THRESHOLD_MINUTES:
        return True
    since_last_poll = (now_utc - state.last_poll_at).total_seconds() / 60
    return since_last_poll >= QUIET_POLL_INTERVAL_MINUTES


def _try_new_signals(client: TradingClient, state: SignalBotState, equity: float,
                      buying_power: float, now_utc: datetime) -> None:
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

        symbol = symbol_for_index(parsed.get("index"))
        if symbol is None:
            continue
        if symbol in state.open_trades:
            print(f"Signal fuer {symbol}, aber bereits eine offene Position - uebersprungen.")
            continue

        try:
            etf_price = get_latest_price(symbol)
        except Exception as e:
            print(f"Konnte aktuellen Kurs fuer {symbol} nicht laden: {e}")
            continue

        signal = build_signal_from_parsed(parsed, etf_price, datetime.now(NY))
        if signal is None:
            continue

        qty = position_size(signal, equity, buying_power)
        if qty < 1:
            print(f"Signal fuer {symbol} erkannt, aber Stueckzahl < 1 - ausgelassen.")
            continue

        order = place_bracket_order(client, symbol, signal, qty)
        state.open_trades[symbol] = OpenSignalTrade(
            signal=signal, order_id=str(order.id), qty=qty, source_message_id=message_id,
        )
        send_notification(
            f"Signal-Einstieg {signal.direction.value} {qty}x {symbol} @ ~{signal.entry_price:.2f} "
            f"(Kanal-Signal), Stop {signal.stop:.2f}, Ziel {signal.target:.2f}"
        )
        buying_power -= qty * etf_price  # grob fuer weitere Signale im selben Lauf gegenrechnen


def main() -> None:
    now = datetime.now(NY)

    kill_switch = check_kill_switch(KILL_SWITCH_PATH)
    if kill_switch is not None:
        print(f"Kill-Switch aktiv: {kill_switch.reason}. Beende ohne weitere Aktion.")
        return

    state = load_state(STATE_PATH)
    client = TradingClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)

    try:
        _run(client, state, now)
    except Exception as e:
        print(f"Unerwarteter Fehler: {e}")
        send_notification(f"Signal-Bot-Fehler: {e}")
        save_state(state, STATE_PATH)
        raise


def _run(client: TradingClient, state: SignalBotState, now: datetime) -> None:
    try:
        account = client.get_account()
        state.consecutive_api_errors = 0
    except Exception as e:
        state.consecutive_api_errors += 1
        print(f"API-Fehler beim Kontoabruf: {e}")
        save_state(state, STATE_PATH)
        return

    equity = float(account.equity)
    buying_power = float(account.buying_power)
    if state.initial_equity is None:
        state.initial_equity = equity

    _check_filled_trades(client, state, now)

    if state.stopped_permanently:
        save_state(state, STATE_PATH)
        return

    total_loss = (equity - state.initial_equity) / state.initial_equity if state.initial_equity else 0.0
    if total_loss <= TOTAL_LOSS_LIMIT:
        print(f"Sicherheitsschalter: Gesamtverlust {total_loss * 100:.1f}%")
        send_notification(f"Signal-Bot Sicherheitsschalter: Gesamtverlust {total_loss * 100:.1f}%, stoppe dauerhaft.")
        _close_all_open(client, state, now, "safety_stop")
        state.stopped_permanently = True
        save_state(state, STATE_PATH)
        return

    if state.consecutive_api_errors >= API_ERROR_LIMIT:
        print(f"Sicherheitsschalter: {state.consecutive_api_errors} API-Fehler in Folge, keine neuen Einstiege.")
        save_state(state, STATE_PATH)
        return

    if now.time() >= EOD_CUTOFF:
        if state.open_trades:
            _close_all_open(client, state, now, "eod")
        save_state(state, STATE_PATH)
        return

    now_utc = datetime.now(timezone.utc)
    market_window_open = (
        _is_eu_hours(now_utc) or _is_us_market_open(client) or _is_us_pre_session(now_utc)
    )
    if market_window_open and _should_poll_channel(state, now_utc):
        _try_new_signals(client, state, equity, buying_power, now_utc)
    else:
        reason = "ausserhalb EU-/US-Handelsfenster" if not market_window_open else "Ruhe-Drosselung aktiv"
        print(f"Kein Kanal-Abruf diesen Lauf ({reason}).")
    save_state(state, STATE_PATH)


if __name__ == "__main__":
    main()
