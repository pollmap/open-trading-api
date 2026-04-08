"""Provider 인터페이스

KIS(한국투자증권) + Upbit(업비트) 듀얼 거래소 지원.
"""

from .base import DataProvider, BrokerageProvider

__all__ = [
    "DataProvider",
    "BrokerageProvider",
]

# Lazy import — upbit은 httpx/websockets 의존
def get_upbit_client(*args, **kwargs):
    """업비트 클라이언트 팩토리 (lazy import)"""
    from kis_backtest.providers.upbit.client import UpbitClient
    return UpbitClient(*args, **kwargs)
