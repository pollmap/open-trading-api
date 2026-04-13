"""Alpaca Markets brokerage provider.

Implements `BrokerageProvider` Protocol with the exact same surface as
`KISBrokerageProvider` so `LiveOrderExecutor` accepts it without changes.
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


class AlpacaBrokerageProvider:
    """Alpaca `BrokerageProvider` implementation.

    Requires: ``pip install alpaca-py``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        paper: bool = True,
    ) -> None:
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide as AlpOrderSide, TimeInForce
        except ImportError as exc:
            raise ImportError(
                "alpaca-py not installed. Run: pip install alpaca-py"
            ) from exc

        self._api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self._api_secret = api_secret or os.environ.get("ALPACA_API_SECRET", "")
        if not self._api_key or not self._api_secret:
            raise ValueError(
                "Alpaca credentials missing. Set ALPACA_API_KEY + ALPACA_API_SECRET."
            )
        self._paper = paper
        self._client = TradingClient(
            api_key=self._api_key,
            secret_key=self._api_secret,
            paper=paper,
        )
        self._OrderRequest = MarketOrderRequest
        self._AlpOrderSide = AlpOrderSide
        self._TimeInForce = TimeInForce
        logger.info("AlpacaBrokerageProvider initialized (paper=%s)", paper)

    def get_balance(self) -> AccountBalance:
        account = self._client.get_account()
        equity = float(account.equity)
        cash = float(account.cash)
        pnl = equity - float(account.last_equity or equity)
        pnl_pct = (pnl / float(account.last_equity)) * 100 if account.last_equity else 0.0
        return AccountBalance(
            total_cash=cash,
            available_cash=float(account.buying_power),
            total_equity=equity,
            total_pnl=pnl,
            total_pnl_percent=pnl_pct,
            currency="USD",
        )

    def get_positions(self) -> List[Position]:
        raw = self._client.get_all_positions()
        positions = []
        for p in raw:
            positions.append(Position(
                symbol=p.symbol,
                quantity=int(float(p.qty)),
                average_price=float(p.avg_entry_price),
                current_price=float(p.current_price or p.avg_entry_price),
                unrealized_pnl=float(p.unrealized_pl or 0),
                unrealized_pnl_percent=float(p.unrealized_plpc or 0) * 100,
                name=p.symbol,
            ))
        return positions

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
    ) -> Order:
        if order_type != OrderType.MARKET:
            raise NotImplementedError(
                "Alpaca provider v1.1 only supports MARKET orders"
            )
        alp_side = (
            self._AlpOrderSide.BUY if side == OrderSide.BUY else self._AlpOrderSide.SELL
        )
        req = self._OrderRequest(
            symbol=symbol,
            qty=quantity,
            side=alp_side,
            time_in_force=self._TimeInForce.DAY,
        )
        result = self._client.submit_order(req)
        logger.info("Alpaca order submitted: %s %s %d", symbol, side.value, quantity)
        return Order(
            id=str(result.id),
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            filled_quantity=int(float(result.filled_qty or 0)),
            average_price=float(result.filled_avg_price or 0),
            status=OrderStatus.SUBMITTED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
