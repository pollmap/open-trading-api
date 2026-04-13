"""yfinance 폴백 어댑터.

nexus-finance MCP 다운 시 2차 소스. 한국 종목은 `005930.KS` 포맷.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """한국 6자리 숫자면 .KS 부착. 그 외는 그대로."""
    if symbol.isdigit() and len(symbol) == 6:
        return f"{symbol}.KS"
    return symbol


@dataclass
class YFinanceAdapter:
    """yfinance OHLCV 어댑터. MCP 폴백 전용."""

    timeout: int = 15

    def get_ohlcv(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        period: str = "1y",
    ) -> pd.DataFrame:
        """OHLCV DataFrame. columns=[Open, High, Low, Close, Volume]."""
        import yfinance as yf

        norm = _normalize_symbol(symbol)
        ticker = yf.Ticker(norm)
        if start and end:
            df = ticker.history(start=start, end=end, auto_adjust=False)
        else:
            df = ticker.history(period=period, auto_adjust=False)

        if df.empty:
            raise ValueError(f"yfinance 빈 응답: {norm}")

        df.index.name = "date"
        keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        return df[keep].copy()

    def get_current_price(self, symbol: str) -> float:
        """최신 종가. 실패 시 0.0."""
        try:
            df = self.get_ohlcv(symbol, period="5d")
            return float(df["Close"].iloc[-1])
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"yfinance 현재가 조회 실패 {symbol}: {exc}")
            return 0.0


_DEFAULT = YFinanceAdapter()


def get_ohlcv(
    symbol: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = "1y",
) -> pd.DataFrame:
    """모듈 레벨 편의 함수."""
    return _DEFAULT.get_ohlcv(symbol, start=start, end=end, period=period)
