"""Alpaca market data provider + PriceProvider adapter."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AlpacaDataProvider:
    """Thin wrapper around alpaca-py StockDataHistoricalClient."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest
        except ImportError as exc:
            raise ImportError(
                "alpaca-py not installed. Run: pip install alpaca-py"
            ) from exc

        self._api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self._api_secret = api_secret or os.environ.get("ALPACA_API_SECRET", "")
        self._client = StockHistoricalDataClient(self._api_key, self._api_secret)
        self._QuoteRequest = StockLatestQuoteRequest

    def get_current_price(self, symbol: str) -> float:
        """최근 mid price 반환."""
        try:
            req = self._QuoteRequest(symbol_or_symbols=symbol)
            quote_map = self._client.get_stock_latest_quote(req)
            quote = quote_map.get(symbol)
            if quote is None:
                return 0.0
            bid = float(quote.bid_price or 0)
            ask = float(quote.ask_price or 0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            return bid or ask or 0.0
        except Exception as exc:
            logger.warning("Alpaca price fetch failed %s: %s", symbol, exc)
            return 0.0


class AlpacaPriceAdapter:
    """`PriceProvider` Protocol adapter — same shape as `_KISPriceAdapter`."""

    def __init__(self, data_provider: AlpacaDataProvider) -> None:
        self._data = data_provider

    def get_current_price(self, symbol: str) -> float:
        return self._data.get_current_price(symbol)
