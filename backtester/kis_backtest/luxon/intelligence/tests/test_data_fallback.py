"""MCP → yfinance 폴백 체인 테스트."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from kis_backtest.luxon.intelligence.data_fallback import DataFallback, _CircuitBreaker


def _df(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "Open": range(n),
        "High": range(n),
        "Low": range(n),
        "Close": range(n),
        "Volume": range(n),
    })


def test_mcp_success_skips_yfinance():
    mcp = MagicMock()
    mcp.call_tool.return_value = {"data": [{"Close": 100}, {"Close": 101}]}
    yf = MagicMock()
    fb = DataFallback(mcp_client=mcp, yf=yf)
    out = fb.get_ohlcv("AAPL")
    assert len(out) == 2
    yf.get_ohlcv.assert_not_called()


def test_mcp_fail_triggers_yfinance():
    mcp = MagicMock()
    mcp.call_tool.side_effect = RuntimeError("MCP down")
    yf = MagicMock()
    yf.get_ohlcv.return_value = _df()
    fb = DataFallback(mcp_client=mcp, yf=yf)
    out = fb.get_ohlcv("AAPL")
    assert len(out) == 5
    yf.get_ohlcv.assert_called_once()


def test_circuit_breaker_opens_after_threshold():
    cb = _CircuitBreaker(threshold=3, cooldown_sec=999)
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open


def test_circuit_breaker_resets_on_success():
    cb = _CircuitBreaker(threshold=3)
    cb.record_failure()
    cb.record_success()
    assert cb.failures == 0


def test_no_mcp_uses_yfinance_directly():
    yf = MagicMock()
    yf.get_ohlcv.return_value = _df()
    fb = DataFallback(mcp_client=None, yf=yf)
    fb.get_ohlcv("AAPL")
    yf.get_ohlcv.assert_called_once()
