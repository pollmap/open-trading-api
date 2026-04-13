"""Interactive Brokers (IBKR) provider — stub for v1.1.

Full implementation requires `ib-insync` and running TWS/IB Gateway.
Current status: API surface defined, TWS integration planned for v1.2.

Env vars:
    IBKR_HOST=127.0.0.1
    IBKR_PORT=7497          # paper: 7497, live: 7496
    IBKR_CLIENT_ID=1

Example skeleton:
    from ib_insync import IB, Stock, MarketOrder
    ib = IB()
    ib.connect(os.environ["IBKR_HOST"], int(os.environ["IBKR_PORT"]), clientId=1)
    contract = Stock("AAPL", "SMART", "USD")
    order = MarketOrder("BUY", 10)
    trade = ib.placeOrder(contract, order)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class IBKRBrokerageProvider:
    """Placeholder. Raises at init until v1.2 integration lands."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "IBKR provider arriving in v1.2. "
            "Track: https://github.com/YOUR_ORG/luxon-terminal/issues?q=ibkr"
        )


__all__ = ["IBKRBrokerageProvider"]
