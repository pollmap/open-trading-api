"""yfinance 폴백 데이터 프로바이더.

nexus-finance MCP 실패 시 2차 소스. pip: yfinance>=0.2.40
"""
from .adapter import YFinanceAdapter, get_ohlcv

__all__ = ["YFinanceAdapter", "get_ohlcv"]
