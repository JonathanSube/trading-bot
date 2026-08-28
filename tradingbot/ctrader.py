"""Order-Platzierung und Kontodaten gegen die cTrader Open API (Pepperstone-
Demokonto), Ersatz fuer die urspruengliche Alpaca/QQQ-DIA-Loesung des
Signal-Bots. Siehe trading-bot-spec.md, Aenderungsprotokoll: drei
REST-basierte Broker-Anlaeufe (OANDA, IG, MetaApi.cloud) scheiterten an
Zugangshuerden bzw. Kosten - die cTrader Open API ist die einzige bisher
gefundene Loesung, die sowohl **kostenlos** ist als auch **keine
KYC-Huerde** fuer ein Demokonto verlangt.

WICHTIGER ARCHITEKTURBRUCH gegenueber tradingbot/oanda.py, tradingbot/ig.py,
tradingbot/metaapi.py (alle reines `requests`, kein SDK): die cTrader Open
API ist kein REST/JSON-API, sondern ein **Protobuf-Protokoll ueber eine
dauerhafte TCP-Verbindung**. Es gibt keine einfache REST-Alternative dafuer -
deshalb wird hier ausnahmsweise die offizielle Bibliothek `ctrader_open_api`
(Twisted-basiert, asynchrones Deferred/Callback-Muster) eingesetzt. Um damit
trotzdem normalen `async`/`await`-Code schreiben zu koennen (und denselben
Event-Loop wie der bestehende Telegram-Abruf in
signalbot/telegram_signals.py zu nutzen statt zwei parallele Event-Loops zu
betreiben), wird Twisteds `asyncioreactor` installiert - das erlaubt,
Twisted-Deferreds per `.asFuture(loop)` zu awaiten.

UNVERIFIZIERT (`help.ctrader.com` war in dieser Umgebung per Netzwerk-Policy
nicht abrufbar, nur pypi.org/project/ctrader-open-api lieferte Basis-Infos) -
vor dem ersten Live-Lauf zwingend mit scripts/place_test_ctrader_order.py zu
pruefen:
  - Exakte Feldnamen/Struktur der Protobuf-Requests unten (Namen wie
    ProtoOANewOrderReq, ProtoOAAmendPositionSLTPReq etc. stammen aus
    allgemeinem Wissen ueber die cTrader Open API, nicht live verifiziert).
  - Volumen-Konvention fuer Order-Groessen (Lots vs. eine symbol-spezifische
    kleinste Einheit) - position_size() liefert vorerst Lots wie bei MetaApi.
  - Preis-/Bilanz-Skalierung (cTrader arbeitet intern oft mit Centibons/
    relativen Deltas statt direkten Fliesskommazahlen) - die Umrechnungen
    unten sind Bestwissen, keine Garantie.
"""

import asyncio
import math
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import requests

from twisted.internet import asyncioreactor
from twisted.internet.error import ReactorAlreadyInstalledError

try:
    asyncioreactor.install()
except ReactorAlreadyInstalledError:  # pragma: no cover - nur bei Mehrfachimport in einem Prozess
    pass

from ctrader_open_api import Client, EndPoints, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAmendPositionSLTPReq,
    ProtoOAApplicationAuthReq,
    ProtoOAClosePositionReq,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetTrendbarsReq,
    ProtoOANewOrderReq,
    ProtoOAReconcileReq,
    ProtoOASymbolsListReq,
    ProtoOATraderReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType,
    ProtoOATradeSide,
    ProtoOATrendbarPeriod,
)

from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction

TOKEN_URL = "https://connect.spotware.com/apps/token"


async def get_access_token() -> str:
    """Tauscht den dauerhaften Refresh-Token (CTRADER_REFRESH_TOKEN, per
    scripts/ctrader_authorize.py einmalig erzeugt) gegen einen kurzlebigen
    Access-Token - reiner REST-Aufruf, kein Twisted noetig."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": os.environ["CTRADER_REFRESH_TOKEN"],
            "client_id": os.environ["CTRADER_CLIENT_ID"],
            "client_secret": os.environ["CTRADER_CLIENT_SECRET"],
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["accessToken"]


@dataclass
class CTraderSession:
    client: Client
    account_id: int
    access_token: str
    _symbol_ids: dict[str, int] | None = field(default=None, repr=False)


async def _send(session_or_client, request):
    """asFuture() statt addCallback-Ketten, damit der Rest des Moduls
    normaler async/await-Code bleiben kann."""
    client = session_or_client.client if isinstance(session_or_client, CTraderSession) else session_or_client
    return await client.send(request).asFuture(asyncio.get_event_loop())


@asynccontextmanager
async def ctrader_session():
    """Uebernimmt Connect -> App-Auth -> Konto automatisch ermitteln (kein
    manuell zu suchendes CTRADER_ACCOUNT_ID-Secret, siehe Moduldocstring
    und trading-bot-spec.md zum MetaApi-Vorfall) -> Account-Auth ->
    Disconnect fuer den gesamten Lauf. Waehlt automatisch das erste
    NICHT-Live-Konto (isLive == False) - bricht mit klarer Fehlermeldung ab,
    falls keins existiert, als Sicherheitsnetz gegen versehentlichen
    Live-Handel."""
    access_token = await get_access_token()
    client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)

    loop = asyncio.get_event_loop()
    connected = loop.create_future()
    client.setConnectedCallback(lambda c: not connected.done() and connected.set_result(None))
    client.startService()
    await connected

    try:
        app_auth_req = ProtoOAApplicationAuthReq()
        app_auth_req.clientId = os.environ["CTRADER_CLIENT_ID"]
        app_auth_req.clientSecret = os.environ["CTRADER_CLIENT_SECRET"]
        await _send(client, app_auth_req)

        accounts_req = ProtoOAGetAccountListByAccessTokenReq()
        accounts_req.accessToken = access_token
        accounts_resp = await _send(client, accounts_req)
        demo_accounts = [a for a in accounts_resp.ctidTraderAccount if not a.isLive]
        if not demo_accounts:
            raise RuntimeError(
                "Kein Demo-Konto (isLive == False) zu diesem cTrader-Token gefunden - "
                "Live-Handel wird hier bewusst nicht unterstuetzt, siehe tradingbot/ctrader.py."
            )
        account_id = demo_accounts[0].ctidTraderAccountId

        account_auth_req = ProtoOAAccountAuthReq()
        account_auth_req.ctidTraderAccountId = account_id
        account_auth_req.accessToken = access_token
        await _send(client, account_auth_req)

        session = CTraderSession(client=client, account_id=account_id, access_token=access_token)
        yield session
    finally:
        client.stopService()


async def get_account_info(session: CTraderSession) -> dict:
    """Kontostand/Waehrung - cTrader liefert Geldbetraege als Integer in
    Centibons (Wert * 100, UNVERIFIZIERT), deshalb /100 unten."""
    req = ProtoOATraderReq()
    req.ctidTraderAccountId = session.account_id
    resp = await _send(session, req)
    trader = resp.trader
    return {"balance": trader.balance / 100, "currency": trader.depositAssetId}


async def list_symbols(session: CTraderSession) -> dict[str, int]:
    """{Symbolname: symbolId} - einmal pro Lauf/Session abgefragt und
    zwischengespeichert (Basis fuer scripts/find_ctrader_symbols.py und
    fuer die Aufloesung der INDEX_TO_SYMBOL-Namen vor jeder Order)."""
    if session._symbol_ids is not None:
        return session._symbol_ids
    req = ProtoOASymbolsListReq()
    req.ctidTraderAccountId = session.account_id
    resp = await _send(session, req)
    session._symbol_ids = {s.symbolName: s.symbolId for s in resp.symbol}
    return session._symbol_ids


async def get_latest_price(session: CTraderSession, symbol_name: str) -> float:
    """Letzter M1-Schlusskurs als Naeherung fuer den aktuellen Kurs (keine
    dauerhafte Spot-Preis-Subscription noetig fuer einen kurzen,
    einmaligen Lauf). Trendbar-Preise sind bei cTrader relativ zum
    'low'-Feld kodiert (deltaClose addiert) und je Symbol unterschiedlich
    skaliert (digits) - UNVERIFIZIERT, vor Go-Live mit
    scripts/place_test_ctrader_order.py gegenpruefen."""
    symbol_ids = await list_symbols(session)
    symbol_id = symbol_ids[symbol_name]

    req = ProtoOAGetTrendbarsReq()
    req.ctidTraderAccountId = session.account_id
    req.symbolId = symbol_id
    req.period = ProtoOATrendbarPeriod.M1
    req.count = 1
    resp = await _send(session, req)
    bar = resp.trendbar[-1]
    return (bar.low + bar.deltaClose) / 100000


def position_size(signal: Signal, equity: float, risk_pct: float = 0.03) -> float:
    """Gleiche Risiko-Regel wie tradingbot/metaapi.py::position_size -
    liefert ein Lot-Volumen, auf 0,01-Lot-Schritte abgerundet. Ob cTrader
    fuer Order-Volumen Lots oder eine symbol-spezifische kleinste Einheit
    erwartet, ist UNVERIFIZIERT (siehe Moduldocstring)."""
    if signal.risk <= 0 or equity <= 0:
        return 0.0
    raw_volume = (equity * risk_pct) / signal.risk
    return math.floor(raw_volume * 100) / 100


async def place_market_order(session: CTraderSession, symbol_name: str, signal: Signal, volume: float) -> int:
    """Market-Order, danach separat Stop/Ziel per
    ProtoOAAmendPositionSLTPReq gesetzt (statt auf die optionalen SL/TP-
    Felder von ProtoOANewOrderReq zu vertrauen, deren Verhalten hier nicht
    verifiziert ist - das Nachtraeglich-Setzen ist ein eindeutig
    dokumentierter Kernvorgang der Open API). Gibt die positionId zurueck."""
    if volume < 0.01:
        raise ValueError("Lot-Volumen < 0.01 - Order darf nicht platziert werden")

    symbol_ids = await list_symbols(session)
    symbol_id = symbol_ids[symbol_name]

    order_req = ProtoOANewOrderReq()
    order_req.ctidTraderAccountId = session.account_id
    order_req.symbolId = symbol_id
    order_req.orderType = ProtoOAOrderType.MARKET
    order_req.tradeSide = ProtoOATradeSide.BUY if signal.direction is Direction.LONG else ProtoOATradeSide.SELL
    order_req.volume = int(volume * 100)
    order_resp = await _send(session, order_req)
    position_id = order_resp.position.positionId

    sltp_req = ProtoOAAmendPositionSLTPReq()
    sltp_req.ctidTraderAccountId = session.account_id
    sltp_req.positionId = position_id
    sltp_req.stopLoss = round(signal.stop, 2)
    sltp_req.takeProfit = round(signal.target, 2)
    await _send(session, sltp_req)

    return position_id


async def get_open_positions(session: CTraderSession) -> dict[str, dict]:
    """{Symbolname: Position-Info} der aktuell offenen Positionen."""
    symbol_ids = await list_symbols(session)
    id_to_name = {v: k for k, v in symbol_ids.items()}

    req = ProtoOAReconcileReq()
    req.ctidTraderAccountId = session.account_id
    resp = await _send(session, req)
    result = {}
    for pos in resp.position:
        name = id_to_name.get(pos.tradeData.symbolId)
        if name is None:
            continue
        result[name] = {
            "positionId": pos.positionId,
            "volume": pos.tradeData.volume / 100,
            "entryPrice": pos.price,
        }
    return result


async def close_position(session: CTraderSession, position_id: int, volume: float) -> float:
    """Fuer Session-Ende-Zwangsschluss und Sicherheitsschalter-Stopps.
    Gibt den (Best-effort) Ausstiegspreis zurueck - cTraders Schliess-
    Bestaetigung selbst tragen den Ausfuehrungspreis vermutlich im
    zurueckgegebenen Deal-Objekt (UNVERIFIZIERT); der Aufrufer hat einen
    eigenen Fallback fuer den Fall, dass das Feld fehlt."""
    req = ProtoOAClosePositionReq()
    req.ctidTraderAccountId = session.account_id
    req.positionId = position_id
    req.volume = int(volume * 100)
    resp = await _send(session, req)
    deal = getattr(resp, "deal", None)
    if deal is not None and getattr(deal, "executionPrice", None):
        return float(deal.executionPrice)
    raise RuntimeError(
        "Ausstiegspreis nicht in der Schliess-Bestaetigung gefunden (siehe "
        "tradingbot/ctrader.py::close_position, UNVERIFIZIERT) - Aufrufer "
        "muss einen Fallback verwenden."
    )
