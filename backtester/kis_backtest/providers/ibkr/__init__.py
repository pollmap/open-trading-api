"""Interactive Brokers (IBKR) provider — v1.2.

Full `BrokerageProvider` + `PriceProvider` implementation backed by
`ib-insync` talking to TWS or IB Gateway.

Install:
    pip install ib-insync

Prerequisites:
    - TWS or IB Gateway running locally
    - API connections enabled: Configure → API → Settings → "Enable ActiveX and Socket Clients"
    - Trusted IP: 127.0.0.1

Env vars:
    IBKR_HOST=127.0.0.1
    IBKR_PORT=7497          # 7497=paper, 7496=live, 4002=gateway paper, 4001=gateway live
    IBKR_CLIENT_ID=1

Usage:
    from kis_backtest.providers.ibkr import IBKRBrokerageProvider, IBKRPriceAdapter
    bro = IBKRBrokerageProvider(paper=True)
    price = IBKRPriceAdapter(bro)
"""
from kis_backtest.providers.ibkr.brokerage import (
    IBKRBrokerageProvider,
    IBKRPriceAdapter,
)

__all__ = ["IBKRBrokerageProvider", "IBKRPriceAdapter"]
