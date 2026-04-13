"""MCP → yfinance 폴백 체인.

우선순위: nexus-finance MCP → yfinance.
circuit breaker: 3회 연속 실패 시 30분 nexus 스킵.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from kis_backtest.providers.yfinance.adapter import YFinanceAdapter

logger = logging.getLogger(__name__)


@dataclass
class _CircuitBreaker:
    failures: int = 0
    opened_at: float = 0.0
    threshold: int = 3
    cooldown_sec: int = 1800

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.time()

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    @property
    def is_open(self) -> bool:
        if self.opened_at == 0:
            return False
        if time.time() - self.opened_at > self.cooldown_sec:
            # 리셋
            self.failures = 0
            self.opened_at = 0.0
            return False
        return True


@dataclass
class DataFallback:
    """1순위 MCP nexus-finance, 2순위 yfinance."""

    mcp_client: Optional[object] = None  # mcp_bridge.MCPClient
    yf: YFinanceAdapter = field(default_factory=YFinanceAdapter)
    breaker: _CircuitBreaker = field(default_factory=_CircuitBreaker)

    def get_ohlcv(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        # 1차: MCP (breaker open이 아니면)
        if self.mcp_client is not None and not self.breaker.is_open:
            try:
                result = self.mcp_client.call_tool(
                    "nexus_finance__get_daily_price",
                    {"symbol": symbol, "start": start, "end": end},
                )
                self.breaker.record_success()
                return _to_dataframe(result)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"MCP 실패, yfinance 폴백: {exc}")
                self.breaker.record_failure()

        # 2차: yfinance
        return self.yf.get_ohlcv(symbol, start=start, end=end)


def _to_dataframe(mcp_result: object) -> pd.DataFrame:
    """MCP 결과 dict → DataFrame. 실패 시 raise."""
    if isinstance(mcp_result, dict) and "data" in mcp_result:
        df = pd.DataFrame(mcp_result["data"])
    elif isinstance(mcp_result, list):
        df = pd.DataFrame(mcp_result)
    else:
        raise ValueError(f"알 수 없는 MCP 응답 형식: {type(mcp_result)}")
    if df.empty:
        raise ValueError("MCP 빈 응답")
    return df


_DEFAULT = DataFallback()


def get_ohlcv(symbol: str, start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
    return _DEFAULT.get_ohlcv(symbol, start=start, end=end)
