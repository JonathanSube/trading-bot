"""Order-Platzierung gegen Alpacas Paper-Trading-API.

Siehe trading-bot-spec.md Abschnitt 1 (Bracket-Order: Entry + Stop + Ziel
in einer Order, Alpaca ueberwacht Stop/Ziel serverseitig) und Abschnitt 8,
Schritt 4 (zunaechst manuell ausgeloest, siehe
scripts/place_test_order.py, noch nicht Teil des automatisierten
Workflows).
"""

import math

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.models import Order, Position
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

from tradingbot.orb_strategy import Signal
from tradingbot.setup_detection import Direction


def position_size(signal: Signal, equity: float, buying_power: float) -> int:
    """Stueckzahl nach der 1%-Risiko-Regel (Abschnitt 1, "Positionsgroesse"),
    zusaetzlich auf die verfuegbare Kaufkraft gedeckelt - ohne den Deckel
    lehnt Alpaca die Order ab, siehe die Hebel-Anmerkung dort."""
    if signal.risk <= 0 or equity <= 0:
        return 0

    risk_based = (equity * 0.01) / signal.risk
    affordable = buying_power / signal.entry_price
    return math.floor(min(risk_based, affordable))


def place_bracket_order(client: TradingClient, symbol: str, signal: Signal, qty: int) -> Order:
    if qty < 1:
        raise ValueError("Stueckzahl < 1 - Order darf laut Abschnitt 1 nicht platziert werden")

    side = OrderSide.BUY if signal.direction is Direction.LONG else OrderSide.SELL

    order_request = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=side,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=round(signal.target, 2)),
        stop_loss=StopLossRequest(stop_price=round(signal.stop, 2)),
    )
    return client.submit_order(order_request)


def close_all_positions(client: TradingClient) -> list[Order]:
    """Fuer den Tagesende-Zwangsschluss (Abschnitt 1, 15:55 ET) und fuer
    Sicherheitsschalter-Stopps, die close_open_positions verlangen
    (Abschnitt 3, siehe tradingbot/safety.py)."""
    positions: list[Position] = client.get_all_positions()
    return [client.close_position(pos.symbol) for pos in positions]
