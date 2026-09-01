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
from datetime import datetime, timedelta, timezone

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

# Siehe ctrader_session()-Docstring: unter dem echten Produktionstakt
# (Neuverbindung alle ~60s) reisst die Verbindung haeufiger ab als unter
# vereinzelten manuellen Tests - vermutlich serverseitiges Rate-Limiting.
RECONNECT_ATTEMPTS = 3
RECONNECT_DELAY_SECONDS = 3

# Siehe get_access_token()-Docstring: voruebergehende Aussetzer des
# cTrader-Auth-Servers (ACCESS_DENIED trotz gueltigem, nicht rotiertem
# Token) wurden live mehrfach beobachtet - meist beim naechsten Versuch
# Sekunden spaeter schon wieder behoben, einmal aber auch noch nach der
# urspruenglichen Kombination (2 Versuche, 5s Pause) fehlgeschlagen,
# obwohl der direkt folgende automatische Lauf (60s spaeter) bereits
# wieder erfolgreich war. Grosszuegiger bemessen, um auch laenger
# andauernde Aussetzer innerhalb eines einzelnen Laufs abzufangen, ohne
# jedes Mal eine Telegram-Fehlermeldung auszuloesen.
TOKEN_RETRY_ATTEMPTS = 4
TOKEN_RETRY_DELAY_SECONDS = 7

# Sicherheitsabstand vor dem tatsaechlichen Ablauf des zwischengespeicherten
# Access-Tokens (siehe get_access_token()) - lieber etwas frueher neu
# tauschen als riskieren, dass der Token waehrend eines laufenden
# cTrader-Handshakes ablaeuft.
ACCESS_TOKEN_SAFETY_MARGIN_SECONDS = 120


def run_ctrader(coro):
    """ZWINGEND anstelle von asyncio.run() verwenden, um irgendeine
    Coroutine auszufuehren, die ctrader_session() nutzt - siehe
    Begruendung beim asyncioreactor.install()-Aufruf oben. asyncio.run()
    wuerde eine neue, vom Reactor unabhaengige Loop erzeugen und den
    Verbindungsaufbau erneut lautlos haengen lassen."""
    return _loop.run_until_complete(coro)


def _persist_secret(secret_name: str, value: str) -> None:
    """Aktualisiert ein GitHub-Actions-Secret dieses Repos direkt per API
    (verschluesselt mit dem repo-eigenen oeffentlichen Schluessel, wie von
    GitHub fuer Secret-Updates vorgeschrieben - siehe docs.github.com,
    "Encrypting secrets for the REST API"). Verallgemeinert aus der
    urspruenglich nur fuer den rotierten Refresh-Token gedachten Funktion,
    wird jetzt auch fuer den zwischengespeicherten Access-Token genutzt
    (siehe get_access_token()).

    Braucht ein EIGENES Personal-Access-Token mit Schreibrecht auf
    Actions-Secrets dieses Repos (GH_SECRETS_PAT) - der von Actions
    automatisch bereitgestellte GITHUB_TOKEN darf das nicht. Laeuft dieses
    Modul lokal oder fehlt das PAT, wird nur geloggt und NICHT
    fehlgeschlagen - das Speichern ist immer ein Best-Effort-Schritt fuer
    den NAECHSTEN Lauf, kein Grund, den aktuellen abzubrechen."""
    pat = os.environ.get("GH_SECRETS_PAT")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not pat or not repository:
        print(
            f"[cTrader] GH_SECRETS_PAT/GITHUB_REPOSITORY nicht gesetzt - "
            f"{secret_name} wird NICHT automatisch gespeichert "
            f"(z.B. bei einem lokalen Lauf normal, kein Fehler)."
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
        encrypted = nacl.public.SealedBox(public_key).encrypt(value.encode("utf-8"))

        put_resp = requests.put(
            f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}",
            headers=headers,
            json={
                "encrypted_value": base64.b64encode(encrypted).decode("utf-8"),
                "key_id": key_data["key_id"],
            },
            timeout=10,
        )
        put_resp.raise_for_status()
        print(f"[cTrader] {secret_name} erfolgreich als GitHub-Secret gespeichert.")
    except Exception as exc:
        print(f"[cTrader] Speichern von {secret_name} fehlgeschlagen: {exc!r}")


async def get_access_token() -> str:
    """Liefert einen gueltigen Access-Token - aus dem Zwischenspeicher
    (CTRADER_ACCESS_TOKEN/CTRADER_ACCESS_TOKEN_EXPIRES_AT-Secrets), falls
    der noch nicht abgelaufen ist, sonst per Tausch des dauerhaften
    Refresh-Tokens (CTRADER_REFRESH_TOKEN, per scripts/ctrader_authorize.py
    einmalig erzeugt).

    WICHTIG (gefunden 31.08.2026, nach WIEDERHOLTEN ACCESS_DENIED-
    Ausfaellen trotz mehrfach erhoehtem Retry-Budget - Symptombekaempfung
    allein reichte nicht, die eigentliche Ursache war vermutlich
    naheliegender: der Bot tauschte bislang bei JEDEM einzelnen Lauf
    (minuetlich!) einen kompletten neuen Access-Token, obwohl cTrader
    laut Token-Antwort ('expiresIn'/'expires_in') eine deutlich laengere
    Gueltigkeit ausstellt. Bis zu 60 Token-Tausche pro Stunde sind ein
    plausibler Grund fuer periodische Rate-Limit-Treffer beim
    Auth-Server, die sich als 'voruebergehende Aussetzer' zeigen,
    tatsaechlich aber ein selbstgemachtes Verkehrsaufkommen sein
    koennten. Ein zwischengespeicherter, wiederverwendeter Access-Token
    (mit Sicherheitsabstand vor dem echten Ablauf, siehe
    ACCESS_TOKEN_SAFETY_MARGIN_SECONDS) braucht im Normalfall gar keinen
    Tausch pro Lauf mehr - das reduziert die Tausch-Frequenz drastisch,
    nicht nur die Fehlerbehandlung danach."""
    cached = _cached_access_token()
    if cached is not None:
        return cached

    last_error: Exception | None = None
    for attempt in range(1, TOKEN_RETRY_ATTEMPTS + 1):
        try:
            return await _exchange_refresh_token()
        except Exception as e:
            last_error = e
            print(f"[cTrader] Token-Tausch {attempt}/{TOKEN_RETRY_ATTEMPTS} fehlgeschlagen: {e}")
            if attempt < TOKEN_RETRY_ATTEMPTS:
                await asyncio.sleep(TOKEN_RETRY_DELAY_SECONDS)
    raise RuntimeError(f"Token-Tausch nach {TOKEN_RETRY_ATTEMPTS} Versuchen weiterhin fehlgeschlagen: {last_error}")


def _cached_access_token() -> str | None:
    """Liest den zwischengespeicherten Access-Token, falls vorhanden und
    (mit Sicherheitsabstand) noch nicht abgelaufen. Lokal/ohne vorherigen
    erfolgreichen Lauf sind die Secrets leer - dann ganz normal per
    Refresh-Token neu tauschen (kein Fehler, siehe get_access_token())."""
    token = os.environ.get("CTRADER_ACCESS_TOKEN")
    expires_at_raw = os.environ.get("CTRADER_ACCESS_TOKEN_EXPIRES_AT")
    if not token or not expires_at_raw:
        return None
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except ValueError:
        return None
    if datetime.now(timezone.utc) >= expires_at - timedelta(seconds=ACCESS_TOKEN_SAFETY_MARGIN_SECONDS):
        return None
    print("[cTrader] Zwischengespeicherten Access-Token wiederverwendet, kein neuer Token-Tausch noetig.")
    return token


async def _exchange_refresh_token() -> str:
    """Ein einzelner Token-Tausch-Versuch, siehe get_access_token()."""
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
            _persist_secret("CTRADER_REFRESH_TOKEN", data[key])
            break

    # Feldname unverifiziert (help.ctrader.com nicht abrufbar) - live
    # beobachtet: der Tausch selbst funktioniert, aber "accessToken"
    # (camelCase, urspruengliche Annahme) existiert nicht in der Antwort.
    # Beide plausiblen Varianten (Standard-OAuth2-Konvention "access_token"
    # vs. cTraders sonst uebliches camelCase) werden probiert; schlaegt
    # beides fehl, wird die komplette Antwort zur Diagnose mitgeloggt statt
    # eines nichtssagenden KeyError.
    access_token = None
    for key in ("accessToken", "access_token"):
        if key in data:
            access_token = data[key]
            break
    if access_token is None:
        raise RuntimeError(f"Kein Access-Token in der Antwort gefunden, Felder: {list(data.keys())} - {data}")

    for key in ("expiresIn", "expires_in"):
        if key in data:
            expires_in_seconds = int(data[key])
            print(f"[cTrader] Access-Token gueltig fuer {expires_in_seconds}s, wird zwischengespeichert.")
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
            _persist_secret("CTRADER_ACCESS_TOKEN", access_token)
            _persist_secret("CTRADER_ACCESS_TOKEN_EXPIRES_AT", expires_at.isoformat())
            break

    return access_token


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
    wird weiterverwendet, nicht dessen ClientService-Verbindungsverwaltung.

    WICHTIG (gefunden 31.08.2026, nach dem Merge nach master unter dem
    echten minuetlichen Produktionstakt statt vereinzelter manueller
    Testlaeufe): die Verbindung wird oft (nicht immer) SOFORT nach
    connectionMade() wieder gekappt (ConnectionLost binnen Sub-
    Millisekunden, bevor auch nur die App-Auth-Anfrage rausgehen kann),
    manchmal auch schon der Verbindungsaufbau selbst mit User-Timeout.
    Sieht nach serverseitigem Verbindungs-Rate-Limiting/Session-Konflikt
    bei so haeufigen Neuverbindungen (alle ~60s) aus - unter den
    vereinzelten manuellen Tests nie beobachtet. Deshalb: bis zu
    RECONNECT_ATTEMPTS Versuche mit kurzer Pause, bevor endgueltig
    aufgegeben wird."""
    access_token = await get_access_token()

    last_error: Exception | None = None
    for attempt in range(1, RECONNECT_ATTEMPTS + 1):
        try:
            session, connector = await _connect_and_authenticate(access_token)
            break
        except Exception as e:
            last_error = e
            print(f"[cTrader] Verbindungsversuch {attempt}/{RECONNECT_ATTEMPTS} fehlgeschlagen: {e}")
            if attempt < RECONNECT_ATTEMPTS:
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)
    else:
        raise RuntimeError(
            f"cTrader-Verbindung nach {RECONNECT_ATTEMPTS} Versuchen weiterhin fehlgeschlagen: {last_error}"
        )

    try:
        yield session
    finally:
        connector.disconnect()


async def _connect_and_authenticate(access_token: str):
    """Ein einzelner Verbindungsversuch: Connect -> App-Auth -> Konto
    automatisch ermitteln -> Account-Auth. Ausgelagert aus
    ctrader_session(), damit ctrader_session() das bei einem fehlgeschlagenen
    Versuch (siehe dortige Docstring) mehrfach probieren kann, ohne jedes
    Mal einen neuen Access-Token zu holen."""
    client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)

    loop = asyncio.get_event_loop()
    connected = loop.create_future()
    protocol_holder: dict = {}
    # Diagnose (31.08.2026): seit dem Merge nach master (minuetlicher Takt
    # per externem Cron statt einzelner manueller Testlaeufe) reisst die
    # Verbindung wiederholt binnen Sub-Millisekunden nach "Verbindungsversuch
    # gestartet" wieder ab (ueber setDisconnectedCallback, nicht
    # clientConnectionFailed) - unklar, ob TcpProtocol.connectionMade()
    # dabei ueberhaupt je feuert. Eigener Log-Eintrag hier, um das von
    # startedConnecting/disconnected zu unterscheiden.
    def _log_connected(c):
        print("[cTrader] TCP/TLS-Verbindung hergestellt (connectionMade)")
        if not connected.done():
            connected.set_result(None)

    client.setConnectedCallback(_log_connected)
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
    except Exception:
        connector.disconnect()
        raise

    return session, connector


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

    await amend_position_sltp(session, position_id, signal.stop, signal.target)

    return position_id


async def amend_position_sltp(session: CTraderSession, position_id: int, stop: float, target: float) -> None:
    """Stop/Ziel einer bestehenden Position setzen oder aendern - genutzt
    beim initialen Einstieg (place_market_order) UND wenn der Kanal seine
    Einstiegsnachricht nachtraeglich per Bearbeitung um Stop/Ziel ergaenzt
    (Nutzerwunsch 01.09.2026, siehe scripts/run_signal_bot.py::
    _check_message_edits) - dort wird bewusst NIE eine neue Order platziert,
    nur diese Funktion auf die bereits offene Position angewendet."""
    sltp_req = ProtoOAAmendPositionSLTPReq()
    sltp_req.ctidTraderAccountId = session.account_id
    sltp_req.positionId = position_id
    sltp_req.stopLoss = round(stop, 2)
    sltp_req.takeProfit = round(target, 2)
    await _send(session, sltp_req)


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
