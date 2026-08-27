"""Order-Platzierung und Kontodaten gegen OANDAs v20-REST-API (Practice-
Account), Ersatz fuer tradingbot/orders.py + den Preis-Teil von
tradingbot/data.py::get_latest_price beim Signal-Bot (siehe
trading-bot-spec.md, Aenderungsprotokoll: Umstieg von Alpaca/QQQ-DIA auf
OANDA/echte Index-CFDs).

Reiner REST-Aufruf per requests statt eines eigenen SDK (oandapyV20 o.ae.),
konsistent mit dem Rest des Projekts (siehe signalbot/parser.py-Docstring).
Nur die Practice-Umgebung ist verdrahtet - dieses Projekt handelt
ausschliesslich Paper/Practice, kein Live-Konto.
"""

import math
import os

import requests

from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

OANDA_BASE_URL = "https://api-fxpractice.oanda.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['OANDA_API_TOKEN']}",
        "Content-Type": "application/json",
    }


def get_account_summary(account_id: str) -> dict:
    """NAV/marginAvailable/unrealizedPL - Ersatz fuer Alpacas
    client.get_account()."""
    resp = requests.get(
        f"{OANDA_BASE_URL}/v3/accounts/{account_id}/summary",
        headers=_headers(), timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["account"]


def get_latest_price(account_id: str, instrument: str) -> float:
    """Mittelwert aus bid/ask des jeweils besten Preises - Basis fuer den
    Entry-Kurs beim Signal-Bot, wie zuvor tradingbot/data.py::get_latest_price
    fuer Alpaca."""
    resp = requests.get(
        f"{OANDA_BASE_URL}/v3/accounts/{account_id}/pricing",
        headers=_headers(), params={"instruments": instrument}, timeout=10,
    )
    resp.raise_for_status()
    price = resp.json()["prices"][0]
    bid = float(price["closeoutBid"])
    ask = float(price["closeoutAsk"])
    return (bid + ask) / 2


def position_size(signal: Signal, equity: float, risk_pct: float = 0.03) -> int:
    """Stueckzahl (OANDA "units") nach der Risiko-Regel: risk_pct des
    Kontostands pro Trade, geteilt durch die Stop-Distanz - gleiche Logik
    wie tradingbot/orders.py::position_size, aber ohne Kaufkraft-Deckelung:
    OANDA-Marginsaetze sind je nach Instrument/Konto ESMA-reguliert und
    unterschiedlich - statt das hier zu hart zu kodieren, lehnt OANDA eine
    zu grosse Order beim Platzieren selbst mit klarer Fehlermeldung ab
    (der Aufrufer faengt das wie jeden anderen API-Fehler ab)."""
    if signal.risk <= 0 or equity <= 0:
        return 0
    return math.floor((equity * risk_pct) / signal.risk)


def place_bracket_order(account_id: str, instrument: str, signal: Signal, units: int) -> str:
    """MARKET-Order mit stopLossOnFill/takeProfitOnFill (OANDA-Pendant zu
    Alpacas Bracket-Order). units ist hier immer positiv (Stueckzahl) - die
    Umrechnung auf OANDAs Vorzeichen-Konvention (positiv=long,
    negativ=short) passiert hier, nicht beim Aufrufer."""
    if units < 1:
        raise ValueError("Stueckzahl < 1 - Order darf nicht platziert werden")

    signed_units = units if signal.direction is Direction.LONG else -units
    order_request = {
        "order": {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(signed_units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": f"{signal.stop:.5f}"},
            "takeProfitOnFill": {"price": f"{signal.target:.5f}"},
        }
    }
    resp = requests.post(
        f"{OANDA_BASE_URL}/v3/accounts/{account_id}/orders",
        headers=_headers(), json=order_request, timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    fill = data.get("orderFillTransaction")
    if fill is None:
        raise RuntimeError(f"Order nicht ausgefuellt: {data}")
    return fill["tradeOpened"]["tradeID"]


def get_open_trades(account_id: str) -> dict[str, dict]:
    """{instrument: trade_dict} der aktuell offenen Trades - OANDA fuellt
    Market-Orders synchron (kein Filled-Polling wie bei Alpaca noetig),
    aber die Bracket-Legs laufen serverseitig weiter, daher trotzdem
    Polling noetig um ein Schliessen durch Stop/Ziel zu erkennen."""
    resp = requests.get(
        f"{OANDA_BASE_URL}/v3/accounts/{account_id}/openTrades",
        headers=_headers(), timeout=10,
    )
    resp.raise_for_status()
    return {trade["instrument"]: trade for trade in resp.json()["trades"]}


def get_closed_trade(account_id: str, trade_id: str) -> dict:
    """realizedPL und averageClosePrice eines bereits geschlossenen Trades -
    Ersatz fuer Alpacas Leg-Status-Auswertung in _check_filled_trades."""
    resp = requests.get(
        f"{OANDA_BASE_URL}/v3/accounts/{account_id}/trades/{trade_id}",
        headers=_headers(), timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["trade"]


def close_trade(account_id: str, trade_id: str) -> dict:
    """Fuer den Session-Ende-Zwangsschluss (pro Instrument, siehe
    scripts/run_signal_bot.py) und fuer Sicherheitsschalter-Stopps."""
    resp = requests.put(
        f"{OANDA_BASE_URL}/v3/accounts/{account_id}/trades/{trade_id}/close",
        headers=_headers(), timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
