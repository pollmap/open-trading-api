"""Crypto.com Exchange v1 brokerage (v1.2).

Signing: HMAC-SHA256(apiKey + method + nonce + params, secret).
Docs: https://exchange-docs.crypto.com/exchange/v1/
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from kis_backtest.models import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.crypto.com/exchange/v1"


def _params_to_str(obj: Any, level: int = 0) -> str:
    """Crypto.com signing helper — deterministic param serialization."""
    if level >= 3:
        return str(obj)
    return_str = ""
    for key in sorted(obj):
        return_str += key
        if obj[key] is None:
            return_str += "null"
        elif isinstance(obj[key], list):
            for sub in obj[key]:
                return_str += _params_to_str(sub, level + 1)
        else:
            return_str += str(obj[key])
    return return_str


class CryptoComBrokerageProvider:
    """`BrokerageProvider` for Crypto.com Exchange (spot)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("CRYPTO_COM_API_KEY", "")
        self._api_secret = api_secret or os.environ.get("CRYPTO_COM_API_SECRET", "")
        if not self._api_key or not self._api_secret:
            raise ValueError(
                "Crypto.com credentials missing. Set CRYPTO_COM_API_KEY + CRYPTO_COM_API_SECRET."
            )
        self._session = requests.Session()

    def _private_request(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        nonce = int(time.time() * 1000)
        req_id = nonce
        params = params or {}

        payload_str = (
            f"{method}{req_id}{self._api_key}"
            f"{_params_to_str(params)}{nonce}"
        )
        sig = hmac.new(
            self._api_secret.encode(),
            payload_str.encode(),
            hashlib.sha256,
        ).hexdigest()

        body = {
            "id": req_id,
            "method": method,
            "api_key": self._api_key,
            "params": params,
            "nonce": nonce,
            "sig": sig,
        }
        resp = self._session.post(
            f"{_BASE_URL}/{method}",
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        data = resp.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"Crypto.com error: {data}")
        return data.get("result", {})

    def get_balance(self) -> AccountBalance:
        result = self._private_request("private/user-balance")
        data = (result.get("data") or [{}])[0]
        total_equity = float(data.get("total_available_balance", 0)) + float(
            data.get("total_position_im", 0)
        )
        available = float(data.get("total_available_balance", 0))
        pnl = float(data.get("total_session_unrealized_pnl", 0))
        return AccountBalance(
            total_cash=available,
            available_cash=available,
            total_equity=total_equity or available,
            total_pnl=pnl,
            total_pnl_percent=(pnl / total_equity * 100) if total_equity else 0.0,
            currency="USD",
        )

    def get_positions(self) -> List[Position]:
        try:
            result = self._private_request("private/get-positions")
        except Exception as exc:
            logger.warning("Crypto.com get-positions failed: %s", exc)
            return []

        positions: List[Position] = []
        for p in result.get("data", []):
            try:
                qty = float(p.get("quantity", 0))
                if qty == 0:
                    continue
                positions.append(Position(
                    symbol=p.get("instrument_name", ""),
                    quantity=int(qty) if qty.is_integer() else qty,
                    average_price=float(p.get("open_position_pnl", 0)),
                    current_price=float(p.get("mark_price", 0)),
                    unrealized_pnl=float(p.get("session_unrealized_pnl", 0)),
                    unrealized_pnl_percent=0.0,
                    name=p.get("instrument_name", ""),
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
        params: Dict[str, Any] = {
            "instrument_name": symbol,
            "side": "BUY" if side == OrderSide.BUY else "SELL",
            "type": "MARKET" if order_type == OrderType.MARKET else "LIMIT",
            "quantity": str(quantity),
        }
        if order_type == OrderType.LIMIT:
            if price is None:
                raise ValueError("LIMIT order requires price")
            params["price"] = str(price)

        result = self._private_request("private/create-order", params)
        order_id = str(result.get("order_id", ""))
        logger.info("Crypto.com order placed: %s %s %d", symbol, side.value, quantity)

        return Order(
            id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            filled_quantity=0,
            average_price=0.0,
            status=OrderStatus.SUBMITTED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )


class CryptoComPriceAdapter:
    """`PriceProvider` for Crypto.com — public ticker endpoint."""

    def __init__(self, brokerage: Optional[CryptoComBrokerageProvider] = None) -> None:
        self._session = (brokerage._session if brokerage else requests.Session())

    def get_current_price(self, symbol: str) -> float:
        try:
            resp = self._session.get(
                f"{_BASE_URL}/public/get-tickers",
                params={"instrument_name": symbol},
                timeout=5,
            )
            data = resp.json().get("result", {}).get("data", [{}])[0]
            last = float(data.get("a", 0) or data.get("k", 0))  # ask price or close
            return last
        except Exception as exc:
            logger.warning("Crypto.com price fetch failed %s: %s", symbol, exc)
            return 0.0
