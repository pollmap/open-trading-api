"""Alpaca Markets provider (v1.1).

US 시장 paper/live trading을 위한 `BrokerageProvider` + `PriceProvider`
프로토콜 구현. KIS와 동일한 인터페이스로 `LiveOrderExecutor`에 주입 가능.

설치:
    pip install alpaca-py

환경변수:
    ALPACA_API_KEY=...
    ALPACA_API_SECRET=...
    ALPACA_PAPER=true          # true: paper trading, false: live

참고:
    https://docs.alpaca.markets/docs/paper-trading
"""
from kis_backtest.providers.alpaca.brokerage import AlpacaBrokerageProvider
from kis_backtest.providers.alpaca.data import AlpacaDataProvider, AlpacaPriceAdapter

__all__ = [
    "AlpacaBrokerageProvider",
    "AlpacaDataProvider",
    "AlpacaPriceAdapter",
]
