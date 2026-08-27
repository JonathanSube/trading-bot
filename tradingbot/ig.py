"""Order-Platzierung und Kontodaten gegen IGs REST-API (Demo-Konto),
Ersatz fuer tradingbot/oanda.py - siehe trading-bot-spec.md,
Aenderungsprotokoll: OANDAs API-Selbstbedienung ("Manage API Access") war
ueber den fuer EU-Kunden erreichten Rechtstraeger (oanda.com/eu-en) nicht
auffindbar, deshalb Umstieg auf IG (eigene REST-API, kein Bridge-Dienst
noetig wie bei MetaTrader). Reiner requests-Aufruf, kein SDK.

Im Unterschied zu OANDA (zustandsloser Bearer-Token) braucht IG eine
Session pro Lauf: _login() authentifiziert sich einmal und liefert die
CST/X-SECURITY-TOKEN-Header, die jede weitere Anfrage in diesem Lauf
mitschicken muss - kein dauerhaftes Token wie bei OANDA/Alpaca.

WICHTIG: Die Epic-Codes fuer NAS100/US30/UK100/DE30 in signalbot/mapping.py
sind unverifiziert (siehe dortiger Kommentar) - vor dem ersten Live-Lauf
mit scripts/find_ig_epics.py gegen den echten Demo-Account pruefen.
"""

import math
import os

import requests

from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"


def login() -> dict:
    """Einmal pro Lauf aufrufen - liefert die Session-Header (X-IG-API-KEY,
    CST, X-SECURITY-TOKEN), die alle folgenden Aufrufe in diesem Modul
    brauchen."""
    resp = requests.post(
        f"{IG_BASE_URL}/session",
        headers={
            "X-IG-API-KEY": os.environ["IG_API_KEY"],
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": "2",
        },
        json={"identifier": os.environ["IG_USERNAME"], "password": os.environ["IG_PASSWORD"]},
        timeout=10,
    )
    resp.raise_for_status()
    return {
        "X-IG-API-KEY": os.environ["IG_API_KEY"],
        "CST": resp.headers["CST"],
        "X-SECURITY-TOKEN": resp.headers["X-SECURITY-TOKEN"],
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
    }


def get_account(session: dict) -> dict:
    """Balance/Waehrung des Demo-Kontos - IG_ACCOUNT_ID waehlt bei
    mehreren Demo-Konten das richtige aus, sonst wird das erste
    zurueckgegebene Konto verwendet."""
    resp = requests.get(f"{IG_BASE_URL}/accounts", headers=session, timeout=10)
    resp.raise_for_status()
    accounts = resp.json()["accounts"]
    account_id = os.environ.get("IG_ACCOUNT_ID")
    if account_id:
        return next(a for a in accounts if a["accountId"] == account_id)
    return accounts[0]


def search_markets(session: dict, search_term: str) -> list[dict]:
    """Marktsuche - Basis fuer scripts/find_ig_epics.py, um die echten
    Epic-Codes vor dem ersten Live-Lauf zu bestimmen."""
    resp = requests.get(
        f"{IG_BASE_URL}/markets", headers={**session, "Version": "1"},
        params={"searchTerm": search_term}, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["markets"]


def get_latest_price(session: dict, epic: str) -> float:
    resp = requests.get(f"{IG_BASE_URL}/markets/{epic}", headers={**session, "Version": "3"}, timeout=10)
    resp.raise_for_status()
    snapshot = resp.json()["snapshot"]
    return (float(snapshot["bid"]) + float(snapshot["offer"])) / 2


def position_size(signal: Signal, equity: float, risk_pct: float = 0.03) -> int:
    """Gleiche Risiko-Regel wie tradingbot/oanda.py::position_size - siehe
    dort zur Begruendung, warum keine Margin-Deckelung per Hebel-Annahme
    erfolgt (IG lehnt eine zu grosse Order bei zu wenig Margin selbst ab)."""
    if signal.risk <= 0 or equity <= 0:
        return 0
    return math.floor((equity * risk_pct) / signal.risk)


def _confirm_deal(session: dict, deal_reference: str) -> dict:
    resp = requests.get(f"{IG_BASE_URL}/confirms/{deal_reference}", headers=session, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("dealStatus") != "ACCEPTED":
        raise RuntimeError(f"Order abgelehnt: {data.get('reason')}")
    return data


def place_bracket_order(session: dict, epic: str, signal: Signal, size: int, currency_code: str) -> str:
    """MARKET-Order mit Stop/Limit (IGs Pendant zu Alpacas Bracket-Order/
    OANDAs stopLossOnFill+takeProfitOnFill). Gibt die dealId zurueck."""
    if size < 1:
        raise ValueError("Stueckzahl < 1 - Order darf nicht platziert werden")

    body = {
        "epic": epic,
        "expiry": "-",
        "direction": "BUY" if signal.direction is Direction.LONG else "SELL",
        "size": size,
        "orderType": "MARKET",
        "timeInForce": "FILL_OR_KILL",
        "guaranteedStop": False,
        "stopLevel": round(signal.stop, 2),
        "limitLevel": round(signal.target, 2),
        "forceOpen": True,
        "currencyCode": currency_code,
    }
    resp = requests.post(
        f"{IG_BASE_URL}/positions/otc", headers={**session, "Version": "2"}, json=body, timeout=10,
    )
    resp.raise_for_status()
    confirmed = _confirm_deal(session, resp.json()["dealReference"])
    return confirmed["dealId"]


def get_open_positions(session: dict) -> dict[str, dict]:
    """{epic: {"dealId", "direction", "size", "level", ...}} der aktuell
    offenen Positionen."""
    resp = requests.get(f"{IG_BASE_URL}/positions", headers={**session, "Version": "2"}, timeout=10)
    resp.raise_for_status()
    return {item["market"]["epic"]: item["position"] for item in resp.json()["positions"]}


def close_position(session: dict, deal_id: str, direction: str, size: int) -> dict:
    """Fuer den Session-Ende-Zwangsschluss (pro Instrument) und fuer
    Sicherheitsschalter-Stopps. direction ist die urspruengliche
    Einstiegsrichtung ("BUY"/"SELL") - die Schliess-Order braucht die
    Gegenrichtung."""
    close_direction = "SELL" if direction == "BUY" else "BUY"
    body = {
        "dealId": deal_id,
        "direction": close_direction,
        "size": size,
        "orderType": "MARKET",
        "timeInForce": "FILL_OR_KILL",
    }
    resp = requests.request(
        "DELETE", f"{IG_BASE_URL}/positions/otc", headers={**session, "Version": "1"},
        json=body, timeout=10,
    )
    resp.raise_for_status()
    return _confirm_deal(session, resp.json()["dealReference"])


def find_closed_position_exit_price(session: dict, epic: str) -> float | None:
    """Sucht die juengste automatisch (per Stop/Ziel) geschlossene
    Transaktion fuer dieses Epic in der Kontohistorie und liefert deren
    Schlusskurs - Ersatz fuer OANDAs get_closed_trade() fuer Positionen,
    die nicht ueber close_position() geschlossen wurden. Best-effort: IGs
    Transaktionshistorie-Feldnamen sind hier nicht live gegen die API
    verifiziert (siehe Modul-Docstring), deshalb mehrere bekannte
    Feldnamen-Varianten probiert. Liefert None, wenn nichts Passendes
    gefunden wird - der Aufrufer hat dafuer einen eigenen Fallback."""
    resp = requests.get(
        f"{IG_BASE_URL}/history/transactions", headers={**session, "Version": "2"},
        params={"type": "ALL_DEAL", "pageSize": 20}, timeout=10,
    )
    resp.raise_for_status()
    for tx in resp.json().get("transactions", []):
        if epic not in tx.get("reference", "") and tx.get("instrumentName", "").replace(" ", "") not in epic:
            continue
        for field in ("closeLevel", "level"):
            if tx.get(field):
                try:
                    return float(tx[field])
                except (TypeError, ValueError):
                    continue
    return None
