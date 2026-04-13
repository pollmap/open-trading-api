"""v1.1 국제화 + 다중 브로커 테스트.

- Market enum 확장 (KOSPI/KOSDAQ + NYSE/NASDAQ/AMEX)
- region/currency property
- US 시장은 sell_tax=0
- market_calendar region-aware
- Alpaca/IBKR provider stub 검증
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kis_backtest.strategies.risk.cost_model import (
    KoreaTransactionCostModel,
    Market,
)


# ── Market enum ────────────────────────────────────────────


def test_market_has_us_exchanges():
    assert Market.NYSE.value == "NYSE"
    assert Market.NASDAQ.value == "NASDAQ"
    assert Market.AMEX.value == "AMEX"


def test_market_region_property():
    assert Market.KOSPI.region == "KR"
    assert Market.KOSDAQ.region == "KR"
    assert Market.NYSE.region == "US"
    assert Market.NASDAQ.region == "US"


def test_market_currency_property():
    assert Market.KOSPI.currency == "KRW"
    assert Market.NASDAQ.currency == "USD"


# ── Cost model — US 시장 ───────────────────────────────────


def test_us_sell_tax_is_zero():
    """미국 시장은 한국식 증권거래세 없음."""
    model = KoreaTransactionCostModel()
    assert model.sell_tax_rate(Market.NYSE) == 0.0
    assert model.sell_tax_rate(Market.NASDAQ) == 0.0


def test_kospi_sell_tax_still_positive():
    """KOSPI는 기존 0.20% 유지."""
    model = KoreaTransactionCostModel()
    assert model.sell_tax_rate(Market.KOSPI) == pytest.approx(0.0020)


def test_us_trade_cost_no_tax():
    model = KoreaTransactionCostModel()
    cost = model.trade_cost(value=10_000, market=Market.NASDAQ, is_sell=True)
    assert cost.tax == 0.0
    assert cost.broker_fee > 0
    assert cost.slippage > 0


# ── Market calendar ────────────────────────────────────────


def test_market_calendar_kr_weekday_open():
    """평일 KST 10:00 = KOSPI 개장."""
    from kis_backtest.utils.market_calendar import is_market_open

    # 2026-04-14 (화) 01:00 UTC = 10:00 KST
    tue_10am_kst = datetime(2026, 4, 14, 1, 0, tzinfo=timezone.utc)
    assert is_market_open(tue_10am_kst, Market.KOSPI) is True
    assert is_market_open(tue_10am_kst, Market.KOSDAQ) is True


def test_market_calendar_us_weekday_open():
    """평일 ET 10:00 = NYSE 개장."""
    from kis_backtest.utils.market_calendar import is_market_open

    # 2026-04-14 (화) 15:00 UTC = 10:00 ET
    tue_10am_et = datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc)
    assert is_market_open(tue_10am_et, Market.NYSE) is True
    assert is_market_open(tue_10am_et, Market.NASDAQ) is True


def test_market_calendar_weekend_closed():
    """주말은 양쪽 모두 closed."""
    from kis_backtest.utils.market_calendar import is_market_open

    # 2026-04-18 (토) 15:00 UTC
    sat = datetime(2026, 4, 18, 15, 0, tzinfo=timezone.utc)
    assert is_market_open(sat, Market.KOSPI) is False
    assert is_market_open(sat, Market.NYSE) is False


def test_market_calendar_kr_before_open():
    """평일 KST 08:00 = 개장 전."""
    from kis_backtest.utils.market_calendar import is_market_open

    tue_8am_kst = datetime(2026, 4, 13, 23, 0, tzinfo=timezone.utc)  # 2026-04-14 08:00 KST
    assert is_market_open(tue_8am_kst, Market.KOSPI) is False


def test_market_calendar_next_open():
    """토요일 → 다음 월요일 09:00 KST."""
    from kis_backtest.utils.market_calendar import next_open

    sat = datetime(2026, 4, 18, 15, 0, tzinfo=timezone.utc)
    nxt = next_open(Market.KOSPI, now=sat)
    # 월요일이어야 함
    assert nxt.weekday() == 0


# ── Alpaca provider stub ───────────────────────────────────


def test_alpaca_provider_requires_credentials(monkeypatch):
    """env var 없으면 ValueError."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    # alpaca-py 미설치 환경에서는 ImportError도 허용
    try:
        from kis_backtest.providers.alpaca import AlpacaBrokerageProvider
    except ImportError:
        pytest.skip("alpaca-py not installed")

    with pytest.raises((ValueError, ImportError)):
        AlpacaBrokerageProvider()


def test_alpaca_import_surface():
    """public API 목록 확인."""
    try:
        from kis_backtest.providers import alpaca
    except ImportError:
        pytest.skip("alpaca-py not installed")

    assert hasattr(alpaca, "AlpacaBrokerageProvider")
    assert hasattr(alpaca, "AlpacaDataProvider")
    assert hasattr(alpaca, "AlpacaPriceAdapter")


# ── IBKR stub ──────────────────────────────────────────────


def test_ibkr_provider_now_importable():
    """v1.2에서 실구현됨 — import OK, TWS 없으면 ImportError/ConnectionError."""
    from kis_backtest.providers.ibkr import IBKRBrokerageProvider
    assert IBKRBrokerageProvider is not None
    # TWS 연결 시도 없이 class만 확인 (실연결 테스트는 integration 마커)
