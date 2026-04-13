"""yfinance 어댑터 단위 테스트 (네트워크 없이)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from kis_backtest.providers.yfinance.adapter import YFinanceAdapter, _normalize_symbol


def test_normalize_korean_ticker():
    assert _normalize_symbol("005930") == "005930.KS"


def test_normalize_us_ticker_unchanged():
    assert _normalize_symbol("AAPL") == "AAPL"


def test_get_ohlcv_empty_raises():
    with patch("yfinance.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = pd.DataFrame()
        with pytest.raises(ValueError, match="빈 응답"):
            YFinanceAdapter().get_ohlcv("FAKE", period="1d")


def test_get_ohlcv_returns_standard_columns():
    df_mock = pd.DataFrame({
        "Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [100],
    })
    with patch("yfinance.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = df_mock
        df = YFinanceAdapter().get_ohlcv("AAPL", period="1d")
    assert set(df.columns) >= {"Open", "High", "Low", "Close", "Volume"}


def test_get_current_price_fallback_zero():
    with patch("yfinance.Ticker") as mock_ticker:
        mock_ticker.return_value.history.side_effect = RuntimeError("network")
        price = YFinanceAdapter().get_current_price("AAPL")
    assert price == 0.0
