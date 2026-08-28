"""Order-Platzierung und Kontodaten gegen MetaApi.cloud (Bridge-Dienst zu
einem beliebigen MetaTrader-4/5-Broker-Demokonto), Ersatz fuer
tradingbot/ig.py - siehe trading-bot-spec.md, Aenderungsprotokoll: IG
verlangt fuer jeden API-Zugang ein verifiziertes (KYC-)Live-Konto, auch
wenn nur das Demokonto gehandelt werden soll - das wollte der Nutzer nicht
durchlaufen, deshalb Umstieg auf MetaTrader ueber MetaApi.cloud (eigenes
MetaApi-Konto ohne Broker-KYC, MetaApi selbst verbindet sich im Hintergrund
per Bridge zum MT4/5-Broker-Demokonto). Reiner requests-Aufruf, kein SDK
(MetaApi empfiehlt fuer produktiven Handel offiziell ihr WebSocket-SDK,
aber die REST-API deckt synchrone Order-Platzierung/-Abfrage ebenfalls ab
und passt zum bestehenden No-SDK-Stil dieses Projekts).

Im Unterschied zu IG (Session-Header pro Lauf) ist MetaApis REST-API
zustandslos wie OANDA: ein einzelner "auth-token"-Header (das persoenliche
MetaApi-API-Token, NICHT das Broker-Passwort) reicht fuer jede Anfrage,
kein Login-Schritt noetig.

WICHTIG: Die Symbol-Namen fuer NAS100/US30/UK100/DAX in
signalbot/mapping.py sind unverifiziert (siehe dortiger Kommentar) - sie
haengen vom konkreten MT4/5-Broker-Server ab, den das MetaApi-Konto
verbindet (unterschiedliche Broker benennen z. B. den DAX-Index-CFD als
"DE40", "GER40" oder "DAX40"). Vor dem ersten Live-Lauf mit
scripts/find_metaapi_symbols.py gegen den echten Account pruefen.

Die MetaApi-Region (Teil der Basis-URL, z. B. "new-york", "london") steht
im MetaApi-Dashboard beim jeweiligen Account - ueber METAAPI_REGION
konfigurierbar, Standard "new-york".
"""

import math
import os

import requests

from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction


def _client_base_url() -> str:
    region = os.environ.get("METAAPI_REGION", "new-york")
    return f"https://mt-client-api-v1.{region}.agiliumtrade.ai"


def _account_id() -> str:
    return os.environ["METAAPI_ACCOUNT_ID"]


def _headers() -> dict:
    return {"auth-token": os.environ["METAAPI_TOKEN"], "Content-Type": "application/json"}


def get_account() -> dict:
    """Kontostand/Waehrung des verbundenen MT4/5-Demokontos (Ersatz fuer
    IGs get_account()) - Felder "balance"/"currency" direkt auf oberster
    Ebene, kein verschachteltes "balance"-Objekt wie bei IG."""
    resp = requests.get(
        f"{_client_base_url()}/users/current/accounts/{_account_id()}/account-information",
        headers=_headers(), timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def list_symbols() -> list[str]:
    """Alle beim Broker-Server verfuegbaren Symbole - Basis fuer
    scripts/find_metaapi_symbols.py, um die echten Index-Symbolnamen vor
    dem ersten Live-Lauf zu bestimmen."""
    resp = requests.get(
        f"{_client_base_url()}/users/current/accounts/{_account_id()}/symbols",
        headers=_headers(), timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_latest_price(symbol: str) -> float:
    resp = requests.get(
        f"{_client_base_url()}/users/current/accounts/{_account_id()}/symbols/{symbol}/current-price",
        headers=_headers(), timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return (float(data["bid"]) + float(data["ask"])) / 2


def position_size(signal: Signal, equity: float, risk_pct: float = 0.03) -> float:
    """Gleiche Risiko-Regel wie tradingbot/ig.py::position_size, aber das
    Ergebnis ist ein Lot-Volumen (MT-Konvention) statt einer Stueckzahl -
    auf 0,01-Lot-Schritte abgerundet (kleinste bei den meisten Brokern
    erlaubte Schrittgroesse; ein zu kleiner/grosser Wert fuer das konkrete
    Symbol wird wie bei IG erst von der Order-Platzierung selbst
    zurueckgewiesen, keine Margin-Vorabpruefung hier)."""
    if signal.risk <= 0 or equity <= 0:
        return 0.0
    raw_volume = (equity * risk_pct) / signal.risk
    return math.floor(raw_volume * 100) / 100


_DONE_CODES = {"TRADE_RETCODE_DONE", "TRADE_RETCODE_DONE_PARTIAL"}


def place_bracket_order(symbol: str, signal: Signal, volume: float) -> str:
    """Market-Order mit Stop/Ziel (MetaApis Pendant zu IGs
    stopLevel/limitLevel). Gibt die positionId zurueck."""
    if volume < 0.01:
        raise ValueError("Lot-Volumen < 0.01 - Order darf nicht platziert werden")

    body = {
        "actionType": "ORDER_TYPE_BUY" if signal.direction is Direction.LONG else "ORDER_TYPE_SELL",
        "symbol": symbol,
        "volume": volume,
        "stopLoss": round(signal.stop, 2),
        "takeProfit": round(signal.target, 2),
    }
    resp = requests.post(
        f"{_client_base_url()}/users/current/accounts/{_account_id()}/trade",
        headers=_headers(), json=body, timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("stringCode") not in _DONE_CODES:
        raise RuntimeError(f"Order abgelehnt: {data.get('stringCode')} {data.get('message')}")
    return data["positionId"]


def get_open_positions() -> dict[str, dict]:
    """{symbol: MetatraderPosition-dict} der aktuell offenen Positionen."""
    resp = requests.get(
        f"{_client_base_url()}/users/current/accounts/{_account_id()}/positions",
        headers=_headers(), timeout=10,
    )
    resp.raise_for_status()
    return {p["symbol"]: p for p in resp.json()}


def close_position(position_id: str) -> dict:
    """Fuer den Session-Ende-Zwangsschluss (pro Instrument) und fuer
    Sicherheitsschalter-Stopps."""
    body = {"actionType": "POSITION_CLOSE_ID", "positionId": position_id}
    resp = requests.post(
        f"{_client_base_url()}/users/current/accounts/{_account_id()}/trade",
        headers=_headers(), json=body, timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("stringCode") not in _DONE_CODES:
        raise RuntimeError(f"Schliessen abgelehnt: {data.get('stringCode')} {data.get('message')}")
    return data


def find_closed_position_exit_price(position_id: str) -> float | None:
    """Schlusskurs einer bereits geschlossenen Position anhand ihrer
    Deal-Historie - im Unterschied zu IGs find_closed_position_exit_price
    (dortige Docstring: Feldnamen-Matching per Instrumentname/Referenz,
    weil IG keinen direkten Positions-Filter bietet) hier gezielt ueber
    die positionId selbst moeglich (MetaApi kennt "Deals je Position"),
    kein Best-effort-Raten noetig. entryType "DEAL_ENTRY_OUT" markiert den
    schliessenden Deal (Gegenstueck zu "DEAL_ENTRY_IN" bei Eroeffnung)."""
    resp = requests.get(
        f"{_client_base_url()}/users/current/accounts/{_account_id()}/history-deals/position/{position_id}",
        headers=_headers(), timeout=10,
    )
    resp.raise_for_status()
    exit_deals = [d for d in resp.json() if d.get("entryType") == "DEAL_ENTRY_OUT"]
    if not exit_deals:
        return None
    return float(exit_deals[-1]["price"])
