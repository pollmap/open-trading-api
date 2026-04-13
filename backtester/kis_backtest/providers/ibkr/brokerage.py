"""IBKR brokerage + price provider (v1.2).

Implements `BrokerageProvider` + `PriceProvider` Protocols using `ib-insync`.
Connects to TWS (7496 live / 7497 paper) or IB Gateway (4001 / 4002).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import List, Optional

from kis_backtest.models import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

logger = logging.getLogger(__name__)

# TWS/Gateway port mapping
_DEFAULT_PAPER_PORT = 7497
_DEFAULT_LIVE_PORT = 7496


class IBKRBrokerageProvider:
    """IBKR `BrokerageProvider` implementation.

    Requires: ``pip install ib-insync`` + TWS/Gateway running.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client_id: Optional[int] = None,
        paper: bool = True,
    ) -> None:
        try:
            from ib_insync import IB
        except ImportError as exc:
            raise ImportError(
                "ib-insync not installed. Run: pip install ib-insync"
            ) from exc

        self._host = host or os.environ.get("IBKR_HOST", "127.0.0.1")
        self._port = port or int(
            os.environ.get("IBKR_PORT", str(_DEFAULT_PAPER_PORT if paper else _DEFAULT_LIVE_PORT))
        )
        self._client_id = client_id or int(os.environ.get("IBKR_CLIENT_ID", "1"))
        self._paper = paper

        self._ib = IB()
        self._ib.connect(self._host, self._port, clientId=self._client_id, timeout=10)
        logger.info(
            "IBKRBrokerageProvider connected: %s:%d (paper=%s, clientId=%d)",
            self._host, self._port, paper, self._client_id,
        )

    def __del__(self):
        try:
            if hasattr(self, "_ib") and self._ib.isConnected():
                self._ib.disconnect()
        except Exception:
            pass

    def get_balance(self) -> AccountBalance:
        try:
            account_values = self._ib.accountValues()
        except Exception as exc:
            logger.error("IBKR accountValues failed: %s", exc)
            return AccountBalance(0, 0, 0, 0, 0, "USD")

        # Aggregate relevant tags
        tags = {av.tag: float(av.value) for av in account_values if av.currency in ("USD", "BASE")}
        total_equity = tags.get("NetLiquidation", 0.0)
        total_cash = tags.get("TotalCashValue", 0.0)
        buying_power = tags.get("BuyingPower", total_cash)
        unrealized_pnl = tags.get("UnrealizedPnL", 0.0)

        return AccountBalance(
            total_cash=total_cash,
            available_cash=buying_power,
            total_equity=total_equity,
            total_pnl=unrealized_pnl,
            total_pnl_percent=(unrealized_pnl / total_equity * 100) if total_equity else 0.0,
            currency="USD",
        )

    def get_positions(self) -> List[Position]:
        raw = self._ib.positions()
        positions: List[Position] = []
        for p in raw:
            try:
                positions.append(Position(
                    symbol=p.contract.symbol,
                    quantity=int(p.position),
                    average_price=float(p.avgCost),
                    current_price=float(getattr(p, "marketPrice", 0) or 0),
                    unrealized_pnl=float(getattr(p, "unrealizedPNL", 0) or 0),
                    unrealized_pnl_percent=0.0,
                    name=p.contract.symbol,
                ))
            except Exception as exc:
                logger.warning("position parse error: %s", exc)
        return positions

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
    ) -> Order:
        from ib_insync import Stock, MarketOrder, LimitOrder

        contract = Stock(symbol, "SMART", "USD")
        self._ib.qualifyContracts(contract)

        action = "BUY" if side == OrderSide.BUY else "SELL"
        if order_type == OrderType.MARKET:
            ib_order = MarketOrder(action, quantity)
        elif order_type == OrderType.LIMIT:
            if price is None:
                raise ValueError("LIMIT order requires price")
            ib_order = LimitOrder(action, quantity, price)
        else:
            raise NotImplementedError(f"Order type {order_type} not supported")

        trade = self._ib.placeOrder(contract, ib_order)
        logger.info("IBKR order placed: %s %s %d @ %s", symbol, action, quantity, order_type.value)

        return Order(
            id=str(trade.order.orderId),
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            filled_quantity=int(trade.orderStatus.filled or 0),
            average_price=float(trade.orderStatus.avgFillPrice or 0),
            status=OrderStatus.SUBMITTED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    def cancel_order(self, order_id: str) -> bool:
        """v1.2 basic cancel — best effort."""
        try:
            for trade in self._ib.openTrades():
                if str(trade.order.orderId) == order_id:
                    self._ib.cancelOrder(trade.order)
                    logger.info("IBKR order cancelled: %s", order_id)
                    return True
            logger.warning("IBKR order not found: %s", order_id)
            return False
        except Exception as exc:
            logger.error("IBKR cancel failed %s: %s", order_id, exc)
            return False


class IBKRPriceAdapter:
    """`PriceProvider` adapter for IBKR — uses snapshot mid price."""

    def __init__(self, brokerage: IBKRBrokerageProvider) -> None:
        self._ib = brokerage._ib

    def get_current_price(self, symbol: str) -> float:
        try:
            from ib_insync import Stock
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            ticker = self._ib.reqMktData(contract, "", False, False)
            self._ib.sleep(1)  # Let tick arrive
            bid = float(ticker.bid or 0)
            ask = float(ticker.ask or 0)
            last = float(ticker.last or 0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            return last or bid or ask or 0.0
        except Exception as exc:
            logger.warning("IBKR price fetch failed %s: %s", symbol, exc)
            return 0.0
