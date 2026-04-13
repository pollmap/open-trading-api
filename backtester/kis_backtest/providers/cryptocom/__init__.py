"""Crypto.com Exchange provider (v1.2).

Implements `BrokerageProvider` + `PriceProvider` via Crypto.com Exchange API v1.
Spot trading only. Requires ``pip install aiohttp`` (most envs already have it).

Env vars:
    CRYPTO_COM_API_KEY=...
    CRYPTO_COM_API_SECRET=...

Reference:
    https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html
"""
from kis_backtest.providers.cryptocom.brokerage import (
    CryptoComBrokerageProvider,
    CryptoComPriceAdapter,
)

__all__ = ["CryptoComBrokerageProvider", "CryptoComPriceAdapter"]
