"""Multi-region market calendar (v1.1 i18n).

한국(KRX) + 미국(NYSE/NASDAQ) 시장 시간 라우팅.
기존 `korean_market.is_market_open()`와 호환되며 region 인자로 확장.

Usage:
    from kis_backtest.utils.market_calendar import is_market_open
    from kis_backtest.strategies.risk.cost_model import Market

    is_market_open(now=None, market=Market.KOSPI)   # 한국 09:00-15:30 KST
    is_market_open(now=None, market=Market.NYSE)    # 미국 09:30-16:00 ET
    is_market_open(now=None, market=Market.NASDAQ)  # 동일
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Optional

from kis_backtest.strategies.risk.cost_model import Market

# Timezone offsets (DST 무시 — production은 zoneinfo 권장)
_KST = timezone(timedelta(hours=9))
_ET = timezone(timedelta(hours=-5))  # EST; DST 적용 시 -4

# Market hours (로컬 시각)
_KR_OPEN = time(9, 0)
_KR_CLOSE = time(15, 30)
_US_OPEN = time(9, 30)
_US_CLOSE = time(16, 0)

# Weekend
_SAT = 5
_SUN = 6


def is_market_open(
    now: Optional[datetime] = None,
    market: Market = Market.KOSPI,
) -> bool:
    """시장 개장 여부 (v1.1: region-aware).

    Args:
        now: 기준 시각 (UTC 또는 tz-aware). None이면 datetime.now(timezone.utc).
        market: KR(KOSPI/KOSDAQ) or US(NYSE/NASDAQ/AMEX).

    Returns:
        True if within market hours on a weekday.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    if market.region == "KR":
        local = current.astimezone(_KST)
        open_t, close_t = _KR_OPEN, _KR_CLOSE
    else:
        local = current.astimezone(_ET)
        open_t, close_t = _US_OPEN, _US_CLOSE

    # Weekend
    if local.weekday() in (_SAT, _SUN):
        return False

    # Within hours
    return open_t <= local.time() < close_t


def next_open(
    market: Market = Market.KOSPI,
    now: Optional[datetime] = None,
) -> datetime:
    """다음 개장 시각 (tz-aware UTC) 반환.

    주말이거나 장 마감 후면 다음 영업일 09:00/09:30.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    tz = _KST if market.region == "KR" else _ET
    open_t = _KR_OPEN if market.region == "KR" else _US_OPEN

    local = current.astimezone(tz)
    candidate = local.replace(
        hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0,
    )
    # 이미 오늘 장 지났거나 오늘이면 내일 이동
    if candidate <= local:
        candidate = candidate + timedelta(days=1)
    # 주말 스킵
    while candidate.weekday() in (_SAT, _SUN):
        candidate = candidate + timedelta(days=1)

    return candidate.astimezone(timezone.utc)


__all__ = ["is_market_open", "next_open"]
