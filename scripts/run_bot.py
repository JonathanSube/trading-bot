"""Hauptskript: der 5-Minuten-Ablauf aus Abschnitt 2, fuer Opening Range
Breakout (Abschnitt 1). Wird von der GitHub-Actions-Workflow
(.github/workflows/trading-bot.yml, Schritt 5) alle 5 Minuten waehrend
der US-Session aufgerufen. Kann auch manuell ausgefuehrt werden, macht
dann genau das, was ein einzelner geplanter Lauf machen wuerde.

Reihenfolge pro Lauf, siehe Abschnitt 2 und 3:
1. Kill-Switch pruefen, bevor irgendeine Order angefasst wird
2. Zustand laden, Konto abfragen, Tages-Rollover
3. Offenen Trade abgleichen: Entry-Fill nachtragen, oder Position
   geschlossen -> protokollieren
4. Sicherheitsschalter pruefen
5. Tagesende (15:55 ET): offene Position zwangsschliessen
6. Sonst, falls nicht blockiert: neue Eroeffnungsspanne/Ausbruch pruefen,
   ggf. Order platzieren
7. Zustand speichern
"""

import os
import sys
import time as time_module
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from alpaca.trading.client import TradingClient

from tradingbot.data import load_alpaca_bars
from tradingbot.notify import get_telegram_commands, send_notification
from tradingbot.orb_strategy import build_signal, check_breakout, detect_opening_range
from tradingbot.orders import place_bracket_order, position_size
from tradingbot.reporting import build_status_report
from tradingbot.safety import check_kill_switch, check_safety_switches
from tradingbot.setup_detection import Bar, Direction
from tradingbot.state import (
    BotState,
    initialize_if_needed,
    load_state,
    record_api_error,
    record_api_success,
    record_trade_result,
    roll_to_new_day_if_needed,
    save_state,
)
from tradingbot.trade_log import TradeLogRow, append_trade

SYMBOL = "QQQ"
NY = ZoneInfo("America/New_York")
STATE_PATH = ROOT / "state.json"
KILL_SWITCH_PATH = ROOT / "STOP"
TRADE_LOG_PATH = ROOT / "trades.csv"
EOD_CUTOFF = time(15, 55)
OR_BARS = 6


def scheduled_run_time(now: datetime) -> datetime:
    """Rundet now auf die zuletzt faellige 5-Minuten-Marke ab, als Annahme
    fuer den geplanten Zeitpunkt dieses Laufs (Abschnitt 6, Grundlage fuer
    lauf_verspaetung)."""
    floored_minute = now.minute - (now.minute % 5)
    return now.replace(minute=floored_minute, second=0, microsecond=0)


def log_and_clear(state: BotState, now: datetime, run_delay: float,
                   exit_price: float, exit_reason: str, level: float) -> None:
    signal = state.open_trade
    qty = state.open_qty or 0
    entry_actual = state.open_entry_fill if state.open_entry_fill is not None else signal.entry_price
    direction_mult = 1 if signal.direction is Direction.LONG else -1
    pnl = direction_mult * (exit_price - entry_actual) * qty
    duration_minutes = (now - signal.entry_timestamp).total_seconds() / 60

    row = TradeLogRow(
        timestamp=signal.entry_timestamp,
        direction=signal.direction,
        level=level,
        entry_planned=signal.entry_price,
        entry_actual=entry_actual,
        stop=signal.stop,
        target=signal.target,
        qty=qty,
        risk=signal.risk,
        exit_reason=exit_reason,
        exit_price=exit_price,
        pnl=pnl,
        duration_minutes=duration_minutes,
        run_delay_minutes=run_delay,
    )
    append_trade(TRADE_LOG_PATH, row)
    record_trade_result(state, pnl)

    exit_labels = {"stop": "Stop getroffen", "target": "Ziel getroffen",
                   "eod": "Tagesende-Schluss", "safety_stop": "Sicherheitsschalter-Schluss"}
    send_notification(
        f"Trade geschlossen: {signal.direction.value.upper()} {SYMBOL}, "
        f"{exit_labels.get(exit_reason, exit_reason)}\n"
        f"Ergebnis: {'+' if pnl >= 0 else ''}{pnl:.2f} $ ({row.pnl_in_r:+.2f} R)"
    )

    state.open_trade = None
    state.open_order_id = None
    state.open_qty = None
    state.open_entry_fill = None


def check_entry_fill(client: TradingClient, state: BotState) -> None:
    """Traegt den tatsaechlichen Fuellpreis der Entry-Order nach, sobald
    bekannt (Market-Order, fuellt in der Regel innerhalb des Laufs oder des
    naechsten)."""
    if state.open_trade is None or state.open_entry_fill is not None:
        return
    order = client.get_order_by_id(state.open_order_id)
    if order.filled_avg_price is not None:
        state.open_entry_fill = float(order.filled_avg_price)


def check_position_closed(client: TradingClient, state: BotState, now: datetime, run_delay: float) -> None:
    """Prueft, ob Stop oder Ziel der Bracket-Order inzwischen gefuellt
    wurden, und protokolliert den Trade, falls ja."""
    if state.open_trade is None or state.open_order_id is None:
        return

    order = client.get_order_by_id(state.open_order_id)
    filled_leg = next((leg for leg in (order.legs or []) if leg.status == "filled"), None)
    if filled_leg is None:
        return

    exit_price = float(filled_leg.filled_avg_price)
    signal = state.open_trade
    is_stop = abs(exit_price - signal.stop) < abs(exit_price - signal.target)
    log_and_clear(state, now, run_delay, exit_price, "stop" if is_stop else "target", signal.stop)


def force_close_open_position(client: TradingClient, state: BotState, now: datetime,
                               run_delay: float, reason: str) -> None:
    if state.open_trade is None:
        return

    # Die Bracket-Order haelt die Stueckzahl fuer ihre offenen Legs (Stop,
    # Ziel) reserviert, auch nachdem der Entry gefuellt ist. Ohne diese
    # Legs zuerst zu canceln, lehnt Alpaca eine zusaetzliche Schliess-Order
    # mit "insufficient qty available" ab (live beobachtet am 25.08.2026,
    # Position blieb dadurch ungeplant ueber Nacht offen). Deshalb erst
    # canceln, dann schliessen, nicht andersrum.
    if state.open_order_id:
        try:
            order = client.get_order_by_id(state.open_order_id)
            for leg in (order.legs or []):
                if leg.status not in ("filled", "canceled", "expired"):
                    client.cancel_order_by_id(leg.id)
        except Exception as e:
            print(f"Konnte Bracket-Legs nicht abfragen/canceln, versuche Schluss trotzdem: {e}")

    positions = client.get_all_positions()
    if any(p.symbol == SYMBOL for p in positions):
        order = client.close_position(SYMBOL)
        # Market-Order zum Schliessen ist meist sofort gefuellt, aber nicht
        # garantiert im selben Moment wie die Antwort - kurz nachfragen,
        # statt sofort auf einen Schaetzwert auszuweichen.
        exit_price = None
        for _ in range(3):
            if order.filled_avg_price is not None:
                exit_price = float(order.filled_avg_price)
                break
            time_module.sleep(2)
            order = client.get_order_by_id(order.id)
        if exit_price is None:
            exit_price = state.open_trade.entry_price  # kein Fuellpreis erhalten, Schaetzwert
    else:
        exit_price = state.open_trade.stop  # Position schon zu, kein besserer Wert verfuegbar

    log_and_clear(state, now, run_delay, exit_price, reason, state.open_trade.stop)


def try_new_entry(client: TradingClient, state: BotState, bars_today: list[Bar],
                   now: datetime, run_delay: float, equity: float, buying_power: float) -> None:
    if state.traded_today or len(bars_today) <= OR_BARS:
        return

    # Gegenpruefung: falls state.json aus irgendeinem Grund nicht mehr zum
    # tatsaechlichen Kontostand passt (z. B. Absturz zwischen Order und
    # Speichern), lieber keine zweite Order riskieren als blind vertrauen.
    if any(p.symbol == SYMBOL for p in client.get_all_positions()):
        print(f"WARNUNG: {SYMBOL}-Position bei Alpaca vorhanden, aber state.json "
              f"kennt keinen offenen Trade. Kein neuer Einstieg, manuell pruefen.")
        send_notification(f"Unerwartete {SYMBOL}-Position ohne passenden Zustand gefunden, "
                           f"neue Einstiege heute pausiert, bitte manuell pruefen.")
        state.traded_today = True
        return

    opening_range = detect_opening_range(bars_today)
    if opening_range is None:
        return

    start_index = OR_BARS
    if state.last_processed_candle is not None:
        for i, b in enumerate(bars_today):
            if b.timestamp > state.last_processed_candle:
                start_index = max(start_index, i)
                break

    for i in range(start_index, len(bars_today) - 1):
        direction = check_breakout(opening_range, bars_today[i])
        if direction is None:
            continue

        signal = build_signal(direction, opening_range, bars_today[i + 1])
        if signal is None:
            break

        qty = position_size(signal, equity, buying_power)
        if qty < 1:
            # Abschnitt 1 verlangt "auslassen und protokollieren" - bislang
            # nur stdout (GitHub-Actions-Log), kein Eintrag in trades.csv,
            # weil das eigene Log nur ausgefuehrte Trades kennt (siehe
            # Abschnitt 9, "Log fuer nicht ausgeloeste Setups"). Bei QQQ und
            # der 1-%-Regel praktisch nie erreichbar (Konto muesste unter
            # ca. 300 $ liegen), daher als bekannte Luecke stehen gelassen.
            print(f"Signal gefunden, aber Stueckzahl < 1 (Risiko {signal.risk}) - ausgelassen.")
            state.traded_today = True
            break

        order = place_bracket_order(client, SYMBOL, signal, qty)
        state.traded_today = True
        state.open_trade = signal
        state.open_order_id = str(order.id)
        state.open_qty = qty
        send_notification(f"ORB-Einstieg {direction.value} {qty}x {SYMBOL} @ ~{signal.entry_price:.2f}, "
                           f"Stop {signal.stop:.2f}, Ziel {signal.target:.2f}")
        break


def send_daily_report(client: TradingClient, state: BotState) -> None:
    """Abschnitt 6: taeglicher Statusbericht, einmal pro Tag, wenn der
    Markt fuer heute geschlossen hat. Gleicher Inhalt wie /status."""
    report = build_status_report(client, state, TRADE_LOG_PATH, SYMBOL)
    send_notification(f"Tagesbericht {state.trading_date}\n{report}")


def handle_telegram_commands(client: TradingClient, state: BotState) -> None:
    commands, new_offset = get_telegram_commands(state.telegram_update_offset)
    state.telegram_update_offset = new_offset

    for command in commands:
        cmd = command.split()[0].lower()
        if cmd == "/status":
            report = build_status_report(client, state, TRADE_LOG_PATH, SYMBOL)
            send_notification(report)
        elif cmd == "/help":
            send_notification("Verfuegbare Befehle:\n/status - aktueller Stand\n/help - diese Uebersicht")


def main() -> None:
    now = datetime.now(NY)
    run_delay = (now - scheduled_run_time(now)).total_seconds() / 60

    kill_switch = check_kill_switch(KILL_SWITCH_PATH)
    if kill_switch is not None:
        print(f"Kill-Switch aktiv: {kill_switch.reason}. Beende ohne weitere Aktion.")
        return

    state = load_state(STATE_PATH)
    client = TradingClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)

    try:
        _run(client, state, now, run_delay)
    except Exception as e:
        # Zustand IMMER sichern, auch bei einem unerwarteten Fehler - sonst
        # dauert die Erholung wie am 25./26.08.2026 unnoetig lange (Position
        # blieb ungeplant ueber Nacht offen, weil der Absturz das Speichern
        # verhinderte, siehe trading-bot-spec.md Abschnitt 9). Danach erneut
        # auslösen, damit der GitHub-Actions-Lauf trotzdem als fehlgeschlagen
        # markiert wird, nicht stillschweigend uebergangen.
        print(f"Unerwarteter Fehler: {e}")
        send_notification(f"Bot-Fehler: {e}")
        save_state(state, STATE_PATH)
        raise


def _run(client: TradingClient, state: BotState, now: datetime, run_delay: float) -> None:
    # /status soll auch ausserhalb der Handelszeit funktionieren, deshalb
    # vor der Markt-Pruefung unten.
    handle_telegram_commands(client, state)

    # Der Workflow ist bewusst grosszuegiger getaktet als die Session
    # (siehe .github/workflows/trading-bot.yml, wegen Sommer-/Winterzeit),
    # deshalb hier ueber Alpacas eigenen Marktkalender pruefen statt selbst
    # Handelstage/-zeiten hartzukodieren (Abschnitt 4). Ausnahme: ein
    # offener Trade muss auch ausserhalb der offiziellen Session noch
    # abgeglichen werden koennen, deshalb kein fruehes Beenden, wenn
    # state.open_trade gesetzt ist.
    try:
        clock = client.get_clock()
        record_api_success(state)
    except Exception as e:
        record_api_error(state)
        print(f"API-Fehler beim Marktkalender-Abruf: {e}")
        save_state(state, STATE_PATH)
        return

    if not clock.is_open and state.open_trade is None:
        if state.trading_date == now.date() and not state.daily_report_sent:
            send_daily_report(client, state)
            state.daily_report_sent = True
        else:
            print("Markt aktuell geschlossen (Feiertag/ausserhalb der Session), nichts zu tun.")
        # Immer speichern, nicht nur im Tagesbericht-Zweig: handle_telegram_commands
        # weiter oben kann telegram_update_offset veraendert haben, sonst wird
        # dieselbe /status-Nachricht bei jedem Lauf erneut beantwortet.
        save_state(state, STATE_PATH)
        return

    try:
        account = client.get_account()
        record_api_success(state)
    except Exception as e:
        record_api_error(state)
        print(f"API-Fehler beim Kontoabruf: {e}")
        save_state(state, STATE_PATH)
        return

    equity = float(account.equity)
    buying_power = float(account.buying_power)

    initialize_if_needed(state, equity)
    roll_to_new_day_if_needed(state, now.date(), equity)

    check_entry_fill(client, state)
    check_position_closed(client, state, now, run_delay)

    safety = check_safety_switches(state, equity)
    if safety is not None:
        print(f"Sicherheitsschalter: {safety.reason}")
        send_notification(f"Sicherheitsschalter ausgeloest: {safety.reason}")
        if safety.close_open_positions:
            force_close_open_position(client, state, now, run_delay, "safety_stop")
        if safety.permanent:
            state.stopped_permanently = True
        state.halted_for_day = True
        save_state(state, STATE_PATH)
        return

    if now.time() >= EOD_CUTOFF:
        force_close_open_position(client, state, now, run_delay, "eod")
        state.last_processed_candle = now
        save_state(state, STATE_PATH)
        return

    try:
        bars_today = [b for b in load_alpaca_bars(SYMBOL, lookback_days=1) if b.timestamp.date() == now.date()]
        record_api_success(state)
    except Exception as e:
        record_api_error(state)
        print(f"API-Fehler beim Bar-Abruf: {e}")
        save_state(state, STATE_PATH)
        return

    try_new_entry(client, state, bars_today, now, run_delay, equity, buying_power)

    if bars_today:
        state.last_processed_candle = bars_today[-1].timestamp
    save_state(state, STATE_PATH)


if __name__ == "__main__":
    main()
