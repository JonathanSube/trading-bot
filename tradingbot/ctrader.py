"""Order-Platzierung und Kontodaten gegen die cTrader Open API (broker-
unabhaengig - aktuell ein Fusion-Markets-Demokonto, urspruenglich
Pepperstone, siehe trading-bot-spec.md zum Wechsel), Ersatz fuer die
urspruengliche Alpaca/QQQ-DIA-Loesung des Signal-Bots. Siehe
trading-bot-spec.md, Aenderungsprotokoll: drei REST-basierte
Broker-Anlaeufe (OANDA, IG, MetaApi.cloud) scheiterten an Zugangshuerden
bzw. Kosten - die cTrader Open API ist die einzige bisher gefundene
Loesung, die sowohl **kostenlos** ist als auch **keine KYC-Huerde** fuer
ein Demokonto verlangt.

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

Der eigentliche Verbindungsaufbau in `ctrader_session()` nutzt bewusst
NICHT `ctrader_open_api.Client.startService()` (basiert auf
`twisted.application.internet.ClientService`) - dessen interner
Scheduler kam unter dem asyncioreactor live nie in Gang (siehe dortige
Docstring). Stattdessen direktes `reactor.connectSSL`, nur die
Nachrichten-Dispatch-Logik von `Client` wird weiterverwendet.

LIVE VERIFIZIERT (31.08.2026, scripts/place_test_ctrader_order.py gegen
den echten Fusion-Markets-Account, siehe trading-bot-spec.md fuer die
vollstaendige Ursachenkette der Verbindungs-Fixes davor): Verbindungsaufbau,
Token-Tausch samt Rotation, Kontostand-Abfrage, Kursabfrage, Marktorder
inkl. SL/TP-Setzen und Positionsabfrage funktionieren wie unten
implementiert. Volumen-Konvention bestaetigt: `place_market_order()`
sendet Lots * 100 als `volume`, `get_open_positions()` liefert exakt
wieder Lots zurueck (0.01 rein, 0.01 raus) - `position_size()`s
Lot-Ausgabe ist also korrekt.

Weiterhin nicht einzeln verifiziert: `close_position()` (fuer
Session-Ende-Zwangsschluss/Sicherheitsschalter) wurde beim Testkauf nicht
ausgeloest - die Test-Position blieb bewusst offen (siehe
scripts/place_test_ctrader_order.py), erst der naechste echte
Schliess-Vorgang (Sicherheitsschalter oder Session-Ende im Signal-Bot-
Lauf) bestaetigt das live.
"""

import asyncio
import base64
import math
import os
import socket as _socket
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import nacl.public
import requests

from twisted.internet import asyncioreactor
from twisted.internet.error import ReactorAlreadyInstalledError

# WICHTIG (Ursache des ueber mehrere Live-Tests hinweg beobachteten
# stillen 30s-Verbindungs-Timeouts, gefunden 31.08.2026 bei genauerer
# Analyse - nicht "ClientService unter asyncioreactor kaputt" wie zuvor
# vermutet, sondern ein klassischer asyncio.get_event_loop()/asyncio.run()-
# Mismatch): asyncioreactor.install() bindet sich beim Import dieses Moduls
# per get_event_loop() an EINE Event-Loop. Wenn die Aufrufer-Skripte
# (scripts/find_ctrader_symbols.py etc.) danach wie ueblich
# `asyncio.run(main())` verwenden, erzeugt asyncio.run() eine KOMPLETT NEUE
# eigene Loop und laesst die vom Reactor gebundene Loop nie laufen - der
# Reactor registriert reactor.connectSSL()-Callbacks also auf einer toten
# Loop, die niemand jemals iteriert. TCP/TLS-Verbindungsaufbau passiert
# dadurch technisch, aber KEIN Callback (weder connected noch disconnected)
# feuert je - exakt das beobachtete Symptom, unabhaengig davon, ob
# ClientService oder der direkte connectSSL-Bypass verwendet wird.
#
# Fix: die Event-Loop hier explizit selbst erzeugen, VOR dem Install als
# aktuelle Loop setzen und dem Reactor explizit uebergeben - Aufrufer
# muessen danach zwingend tradingbot.ctrader.run_ctrader(coro) statt
# asyncio.run(coro) verwenden, damit dieselbe Loop tatsaechlich laeuft.
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)

try:
    asyncioreactor.install(eventloop=_loop)
except ReactorAlreadyInstalledError:  # pragma: no cover - nur bei Mehrfachimport in einem Prozess
    pass

from twisted.internet import defer, reactor
from twisted.internet import ssl as twisted_ssl

from ctrader_open_api import Client, EndPoints, TcpProtocol
from ctrader_open_api.factory import Factory as _CTraderFactory
from ctrader_open_api.protobuf import Protobuf
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

# Zurueckgesetzt auf connect.spotware.com (31.08.2026): der Wechsel auf
# das im installierten ctrader_open_api-Paket hinterlegte
# openapi.ctrader.com lieferte live einen ACCESS_DENIED-Fehler - der
# Refresh-Token wurde ueber den Browser-Autorisierungs-Ablauf auf
# connect.spotware.com ausgestellt, das ist also offenbar ein
# EIGENSTAENDIGES Autorisierungssystem, kein blosser Alias von
# openapi.ctrader.com (trotz beider Domains von Spotware). connect.spotware.com
# ist der live bestaetigt funktionierende Endpunkt fuer den Autorisierungs-
# Ablauf und wird deshalb auch fuer den Token-Tausch verwendet.
TOKEN_URL = "https://connect.spotware.com/apps/token"


def run_ctrader(coro):
    """ZWINGEND anstelle von asyncio.run() verwenden, um irgendeine
    Coroutine auszufuehren, die ctrader_session() nutzt - siehe
    Begruendung beim asyncioreactor.install()-Aufruf oben. asyncio.run()
    wuerde eine neue, vom Reactor unabhaengige Loop erzeugen und den
    Verbindungsaufbau erneut lautlos haengen lassen."""
    return _loop.run_until_complete(coro)


def _persist_rotated_refresh_token(new_refresh_token: str) -> None:
    """cTrader rotiert den Refresh-Token bei JEDEM Tausch (live bestaetigt
    31.08.2026, siehe get_access_token()) - ohne das hier zu speichern,
    wuerde bereits der naechste automatisierte Lauf mit dem in
    CTRADER_REFRESH_TOKEN hinterlegten, dann schon ungueltigen alten Token
    scheitern. Aktualisiert das GitHub-Actions-Secret direkt per API
    (verschluesselt mit dem repo-eigenen oeffentlichen Schluessel, wie von
    GitHub fuer Secret-Updates vorgeschrieben - siehe docs.github.com,
    "Encrypting secrets for the REST API").

    Braucht ein EIGENES Personal-Access-Token mit Schreibrecht auf
    Actions-Secrets dieses Repos (GH_SECRETS_PAT) - der von Actions
    automatisch bereitgestellte GITHUB_TOKEN darf das nicht. Laeuft dieses
    Modul lokal oder fehlt das PAT, wird nur geloggt und NICHT
    fehlgeschlagen - der frisch getauschte Access-Token ist fuer den
    aktuellen Lauf trotzdem gueltig, nur der naechste Lauf braeuchte dann
    wieder einen manuell neu eingetragenen Refresh-Token."""
    pat = os.environ.get("GH_SECRETS_PAT")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not pat or not repository:
        print(
            "[cTrader] GH_SECRETS_PAT/GITHUB_REPOSITORY nicht gesetzt - "
            "rotierter Refresh-Token wird NICHT automatisch gespeichert "
            "(z.B. bei einem lokalen Lauf normal, kein Fehler)."
        )
        return

    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        key_resp = requests.get(
            f"https://api.github.com/repos/{repository}/actions/secrets/public-key",
            headers=headers,
            timeout=10,
        )
        key_resp.raise_for_status()
        key_data = key_resp.json()

        public_key = nacl.public.PublicKey(base64.b64decode(key_data["key"]))
        encrypted = nacl.public.SealedBox(public_key).encrypt(new_refresh_token.encode("utf-8"))

        put_resp = requests.put(
            f"https://api.github.com/repos/{repository}/actions/secrets/CTRADER_REFRESH_TOKEN",
            headers=headers,
            json={
                "encrypted_value": base64.b64encode(encrypted).decode("utf-8"),
                "key_id": key_data["key_id"],
            },
            timeout=10,
        )
        put_resp.raise_for_status()
        print("[cTrader] Rotierter Refresh-Token erfolgreich als GitHub-Secret gespeichert.")
    except Exception as exc:
        # Bewusst nicht weitergereicht - der aktuelle Lauf hat bereits einen
        # gueltigen Access-Token, das Speichern ist ein Best-Effort-Schritt
        # fuer den NAECHSTEN Lauf, kein Grund, den jetzigen abzubrechen.
        print(f"[cTrader] Speichern des rotierten Refresh-Tokens fehlgeschlagen: {exc!r}")


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
    data = resp.json()
    # Diagnose: nur die Feldnamen mitloggen (nie die Werte - das waeren
    # aktive Tokens/Secrets).
    print(f"[cTrader] Token-Antwort-Felder: {sorted(data.keys())}")

    for key in ("refreshToken", "refresh_token"):
        if key in data:
            _persist_rotated_refresh_token(data[key])
            break

    # Feldname unverifiziert (help.ctrader.com nicht abrufbar) - live
    # beobachtet: der Tausch selbst funktioniert, aber "accessToken"
    # (camelCase, urspruengliche Annahme) existiert nicht in der Antwort.
    # Beide plausiblen Varianten (Standard-OAuth2-Konvention "access_token"
    # vs. cTraders sonst uebliches camelCase) werden probiert; schlaegt
    # beides fehl, wird die komplette Antwort zur Diagnose mitgeloggt statt
    # eines nichtssagenden KeyError.
    for key in ("accessToken", "access_token"):
        if key in data:
            return data[key]
    raise RuntimeError(f"Kein Access-Token in der Antwort gefunden, Felder: {list(data.keys())} - {data}")


@dataclass
class CTraderSession:
    client: Client
    protocol: object  # verbundene TcpProtocol-Instanz, siehe ctrader_session()
    account_id: int
    access_token: str
    _symbol_ids: dict[str, int] | None = field(default=None, repr=False)


def _protocol_send(client: Client, protocol, message, response_timeout: int = 10):
    """Ersatz fuer Client.send() (das auf ClientService.whenConnected()
    basiert) - sendet direkt ueber die bereits verbundene Protocol-
    Instanz. Siehe ctrader_session() zur Begruendung, warum
    ClientService hier umgangen wird. Nutzt weiterhin
    client._responseDeferreds/_received (unveraendert aus
    ctrader_open_api.Client) fuer die Antwort-Zuordnung."""
    response_deferred = defer.Deferred()
    client_msg_id = str(id(response_deferred))
    client._responseDeferreds[client_msg_id] = response_deferred
    response_deferred.addTimeout(response_timeout, reactor)
    protocol.send(message, instant=True, clientMsgId=client_msg_id)
    return response_deferred


async def _send(session: CTraderSession, request):
    """asFuture() statt addCallback-Ketten, damit der Rest des Moduls
    normaler async/await-Code bleiben kann.

    WICHTIG (gefunden 31.08.2026 bei Code-Review vor dem naechsten Live-
    Test, noch nicht live beobachtet): TcpProtocol.stringReceived() liefert
    nur den rohen ProtoMessage-Umschlag (Felder: payloadType, payload als
    Bytes, clientMsgId) an _received()/die Response-Deferred weiter - NICHT
    das eigentliche decodierte Antwortobjekt (z.B. ProtoOATraderRes). Das
    muss explizit per Protobuf.extract() ausgepackt werden, siehe
    ctrader_open_api/protobuf.py. Ohne das wuerde z.B.
    `accounts_resp.ctidTraderAccount` weiter unten mit AttributeError
    fehlschlagen, weil der rohe Umschlag dieses Feld gar nicht hat."""
    deferred = _protocol_send(session.client, session.protocol, request)
    envelope = await deferred.asFuture(asyncio.get_event_loop())
    payload = Protobuf.extract(envelope)
    # cTrader antwortet bei Fehlern (falsche Client-Id/Secret, ungueltiger
    # Access-Token, etc.) mit ProtoErrorRes/ProtoOAErrorRes statt der
    # erwarteten Antwortklasse - ohne diese Pruefung wuerde der Aufrufer
    # erst beim naechsten Feldzugriff mit einem verwirrenden AttributeError
    # scheitern statt der eigentlichen Fehlermeldung von cTrader.
    if "Error" in type(payload).__name__:
        raise RuntimeError(f"cTrader-Fehlerantwort auf {type(request).__name__}: {payload}")
    return payload


@asynccontextmanager
async def ctrader_session():
    """Uebernimmt Connect -> App-Auth -> Konto automatisch ermitteln (kein
    manuell zu suchendes CTRADER_ACCOUNT_ID-Secret, siehe Moduldocstring
    und trading-bot-spec.md zum MetaApi-Vorfall) -> Account-Auth ->
    Disconnect fuer den gesamten Lauf. Waehlt automatisch das erste
    NICHT-Live-Konto (isLive == False) - bricht mit klarer Fehlermeldung ab,
    falls keins existiert, als Sicherheitsnetz gegen versehentlichen
    Live-Handel.

    Verbindet BEWUSST NICHT ueber ctrader_open_api.Client.startService()
    (das ist ein twisted.application.internet.ClientService) - live
    beobachtet (31.08.2026): dessen interner Scheduler kam unter dem
    installierten asyncioreactor nie in Gang (weder Connected- noch
    Disconnected-Callback feuerten, 30s Timeout ohne jede Aktivitaet),
    obwohl ein reiner TCP/TLS-Socket zum selben Host/Port im selben Lauf
    sofort erfolgreich war (siehe trading-bot-spec.md). Stattdessen wird
    hier direkt reactor.connectSSL verwendet (die grundlegende,
    reactor-agnostische Twisted-API), nur die Nachrichten-Dispatch-Logik
    von Client (_connected/_disconnected/_received/_responseDeferreds)
    wird weiterverwendet, nicht dessen ClientService-Verbindungsverwaltung."""
    access_token = await get_access_token()
    client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)

    loop = asyncio.get_event_loop()
    connected = loop.create_future()
    protocol_holder: dict = {}
    client.setConnectedCallback(lambda c: not connected.done() and connected.set_result(None))
    client.setDisconnectedCallback(
        lambda c, reason: print(f"[cTrader] Verbindung getrennt/fehlgeschlagen: {reason}")
    )

    # forProtocol() (nicht der direkte Konstruktor) setzt factory.protocol
    # auf die TcpProtocol-Klasse - Factory.__init__ selbst ignoriert das
    # *args-Positionsargument (siehe ctrader_open_api/factory.py), genau
    # wie im Original Client.__init__ (Factory.forProtocol(protocol,
    # client=self)).
    factory = _CTraderFactory.forProtocol(TcpProtocol, client=client)
    _original_factory_connected = factory.connected

    def _capture_protocol(protocol):
        protocol_holder["protocol"] = protocol
        _original_factory_connected(protocol)

    factory.connected = _capture_protocol

    # WICHTIG (gefunden 31.08.2026, nach zwei Live-Tests mit weiterhin
    # lautlosem 30s-Timeout trotz des Event-Loop-Fixes oben): twisted.
    # internet.protocol.ClientFactory.clientConnectionFailed ist per
    # Default ein reiner No-Op-Stub. Wenn der Verbindungsversuch SELBST
    # fehlschlaegt (TCP-Connect oder TLS-Handshake, bevor ueberhaupt ein
    # Protocol verbunden wird), passiert dadurch OHNE diesen Hook absolut
    # gar nichts - keine Exception, kein Log, kein Disconnected-Callback
    # (der laeuft nur ueber TcpProtocol.connectionLost(), das aber nie
    # aufgerufen wird, wenn nie eine Verbindung zustande kam). Das erklaert
    # die komplette Stille bei jedem bisherigen Testlauf, auch nach dem
    # Event-Loop-Fix - der eigentliche Fehler wurde nie sichtbar.
    def _connection_failed(connector_, reason):
        print(f"[cTrader] Verbindungsversuch fehlgeschlagen: {reason}")
        if not connected.done():
            connected.set_exception(RuntimeError(f"cTrader-Verbindungsversuch fehlgeschlagen: {reason}"))

    factory.clientConnectionFailed = _connection_failed

    def _started_connecting(connector_):
        print(f"[cTrader] Verbindungsversuch gestartet zu {connector_.getDestination()}")

    factory.startedConnecting = _started_connecting

    # WICHTIG (gefunden 31.08.2026, dritter Anlauf): der clientConnection
    # Failed-Hook oben zeigte den echten Fehler - "twisted.internet.error.
    # TimeoutError: User timeout caused connection failure" nach den vollen
    # 30s, obwohl ein reiner TCP/TLS-Socket zum selben Host/Port im selben
    # Job sofort erfolgreich war. Twisteds Standard-Resolver
    # (base.BlockingResolver) loest den Hostnamen ueber das aeltere,
    # rein IPv4-taugliche socket.gethostbyname() auf statt ueber
    # socket.getaddrinfo() - falls das in dieser Netzwerkumgebung anders
    # reagiert (haengt/liefert eine andere, nicht erreichbare Adresse) als
    # der erfolgreiche Diagnose-Schritt (der getaddrinfo()-basiertes
    # socket.create_connection() nutzt), waere das eine plausible
    # Erklaerung. Um das auszuschliessen, wird hier selbst per
    # gethostbyname() aufgeloest (identischer Mechanismus zu Twisteds
    # eigenem Resolver, aber synchron VOR dem Verbindungsaufbau, mit
    # Log-Zeile) und die IP-Adresse direkt an connectSSL uebergeben - der
    # Hostname bleibt fuer TLS-SNI/Zertifikatspruefung in
    # optionsForClientTLS() unveraendert.
    resolved_ip = _socket.gethostbyname(EndPoints.PROTOBUF_DEMO_HOST)
    print(f"[cTrader] {EndPoints.PROTOBUF_DEMO_HOST} selbst aufgeloest zu {resolved_ip}")

    context_factory = twisted_ssl.optionsForClientTLS(EndPoints.PROTOBUF_DEMO_HOST)
    connector = reactor.connectSSL(
        resolved_ip, EndPoints.PROTOBUF_PORT, factory, context_factory, timeout=15
    )

    try:
        await asyncio.wait_for(connected, timeout=30)
    except RuntimeError:
        # von _connection_failed oben ausgeloest - Nachricht traegt bereits
        # den echten Fehlgrund von Twisted, einfach durchreichen.
        connector.disconnect()
        raise
    except asyncio.TimeoutError:
        connector.disconnect()
        raise RuntimeError(
            f"Keine Verbindung zu {EndPoints.PROTOBUF_DEMO_HOST}:{EndPoints.PROTOBUF_PORT} "
            "innerhalb von 30s zustande gekommen - moegliche Ursachen: Netzwerk-/"
            "Firewall-Problem, TLS-Handshake schlaegt fehl (siehe requirements.txt: "
            "service_identity noetig), oder der Host/Port ist nicht (mehr) korrekt. "
            "Siehe vorherige [cTrader]-Log-Zeilen fuer den genauen Fehlgrund."
        )

    try:
        session = CTraderSession(
            client=client, protocol=protocol_holder["protocol"],
            account_id=0, access_token=access_token,
        )

        app_auth_req = ProtoOAApplicationAuthReq()
        app_auth_req.clientId = os.environ["CTRADER_CLIENT_ID"]
        app_auth_req.clientSecret = os.environ["CTRADER_CLIENT_SECRET"]
        await _send(session, app_auth_req)

        accounts_req = ProtoOAGetAccountListByAccessTokenReq()
        accounts_req.accessToken = access_token
        accounts_resp = await _send(session, accounts_req)
        demo_accounts = [a for a in accounts_resp.ctidTraderAccount if not a.isLive]
        if not demo_accounts:
            raise RuntimeError(
                "Kein Demo-Konto (isLive == False) zu diesem cTrader-Token gefunden - "
                "Live-Handel wird hier bewusst nicht unterstuetzt, siehe tradingbot/ctrader.py."
            )
        session.account_id = demo_accounts[0].ctidTraderAccountId

        account_auth_req = ProtoOAAccountAuthReq()
        account_auth_req.ctidTraderAccountId = session.account_id
        account_auth_req.accessToken = access_token
        await _send(session, account_auth_req)

        yield session
    finally:
        connector.disconnect()


async def get_account_info(session: CTraderSession) -> dict:
    """Kontostand/Waehrung - cTrader liefert Geldbetraege als Integer in
    Centibons (Wert * 100), deshalb /100 unten. Live bestaetigt
    (31.08.2026): Demokonto-Balance 10000.0 korrekt zurueckgegeben."""
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
    'low'-Feld kodiert (deltaClose addiert), Skalierung /100000 live
    bestaetigt (31.08.2026: NAS100 ergab einen plausiblen Kurs von
    29365.27)."""
    symbol_ids = await list_symbols(session)
    symbol_id = symbol_ids[symbol_name]

    # fromTimestamp/toTimestamp sind PFLICHTFELDER (live 31.08.2026 per
    # EncodeError entdeckt, in keiner hier verfuegbaren Doku genannt) -
    # Millisekunden seit Epoch. 30-Minuten-Fenster bis jetzt, reicht
    # sicher fuer mindestens eine M1-Kerze auch bei kurzen Verbindungs-
    # Verzoegerungen.
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    req = ProtoOAGetTrendbarsReq()
    req.ctidTraderAccountId = session.account_id
    req.symbolId = symbol_id
    req.period = ProtoOATrendbarPeriod.M1
    req.fromTimestamp = now_ms - 30 * 60 * 1000
    req.toTimestamp = now_ms
    resp = await _send(session, req)
    bar = resp.trendbar[-1]
    return (bar.low + bar.deltaClose) / 100000


def position_size(signal: Signal, equity: float, risk_pct: float = 0.03) -> float:
    """Gleiche Risiko-Regel wie tradingbot/metaapi.py::position_size -
    liefert ein Lot-Volumen, auf 0,01-Lot-Schritte abgerundet. Lots als
    Volumen-Einheit live bestaetigt (31.08.2026, siehe Moduldocstring)."""
    if signal.risk <= 0 or equity <= 0:
        return 0.0
    raw_volume = (equity * risk_pct) / signal.risk
    return math.floor(raw_volume * 100) / 100


async def place_market_order(session: CTraderSession, symbol_name: str, signal: Signal, volume: float) -> int:
    """Market-Order, danach separat Stop/Ziel per
    ProtoOAAmendPositionSLTPReq gesetzt (statt auf die optionalen SL/TP-
    Felder von ProtoOANewOrderReq zu vertrauen - das Nachtraeglich-Setzen
    ist ein eindeutig dokumentierter Kernvorgang der Open API). Live
    bestaetigt (31.08.2026): Order + SLTP-Aufruf liefen ohne Fehlerantwort
    durch, get_open_positions() zeigte die Position danach korrekt mit
    0.01 Lot. Gibt die positionId zurueck."""
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
    Das Schliessen selbst live bestaetigt (31.08.2026): die Test-Position
    aus scripts/place_test_ctrader_order.py war nach diesem Aufruf
    nachweislich nicht mehr in get_open_positions() - trotz der Exception
    unten, die nur die Ausstiegspreis-Ermittlung betrifft, nicht das
    Schliessen selbst. Gibt den (Best-effort) Ausstiegspreis zurueck -
    die vermutete deal.executionPrice-Struktur stimmte live nicht (siehe
    Diagnose-Log unten), noch nicht durch eine zweite Test-Order mit
    Log-Auswertung nachgebessert. scripts/run_signal_bot.py faengt das
    bereits robust ab (Fallback auf den Entry-Preis, siehe dort) - nicht
    blockierend fuer den Go-Live, nur die Trade-Log-Genauigkeit leidet in
    diesem Randfall."""
    req = ProtoOAClosePositionReq()
    req.ctidTraderAccountId = session.account_id
    req.positionId = position_id
    req.volume = int(volume * 100)
    resp = await _send(session, req)
    # Diagnose (31.08.2026): die Order wurde live nachweislich angenommen
    # (keine Fehlerantwort, siehe _send()) - nur die erwartete
    # deal.executionPrice-Struktur stimmte nicht. Volle Antwort mitloggen,
    # um die tatsaechlichen Feldnamen zu finden statt weiter zu raten.
    print(f"[cTrader] close_position()-Antwort: {resp}")
    deal = getattr(resp, "deal", None)
    if deal is not None and getattr(deal, "executionPrice", None):
        return float(deal.executionPrice)
    raise RuntimeError(
        "Ausstiegspreis nicht in der Schliess-Bestaetigung gefunden (siehe "
        "tradingbot/ctrader.py::close_position, UNVERIFIZIERT) - Aufrufer "
        "muss einen Fallback verwenden."
    )
