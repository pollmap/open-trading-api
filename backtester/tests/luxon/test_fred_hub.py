"""Sprint 1 — FREDHub (A1) 단위 테스트.

MCP 호출 패턴 + 캐시 통합 + 변환 + staleness 감지 검증.
모든 테스트는 FakeMCPProvider를 사용 (실제 MCP 서버 불필요).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from kis_backtest.luxon.stream.fred_cache import FREDCache
from kis_backtest.luxon.stream.fred_hub import (
    FREDHub,
    FredSeriesRegistry,
    _apply_transform,
    _parse_fred_mcp_response,
)
from kis_backtest.luxon.stream.schema import (
    FredSeries,
    FredSeriesId,
    FredSource,
    SeriesCategory,
    StalenessReport,
    TransformType,
)
from .conftest import FakeMCPProvider


def _build_hub(
    fake_mcp: FakeMCPProvider, tmp_cache_dir: Path
) -> FREDHub:
    """테스트용 FREDHub 생성 (실제 registry + tmp cache)."""
    cache = FREDCache(cache_dir=tmp_cache_dir, ttl_hours=6)
    registry = FredSeriesRegistry.load()
    return FREDHub(mcp=fake_mcp, cache=cache, registry=registry)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_load_series_calls_mcp_macro_fred(
    fake_mcp: FakeMCPProvider, tmp_cache_dir: Path
) -> None:
    """load_series()가 MCP macro_fred 도구를 정확한 인자로 호출.

    nexus-finance MCP의 실제 도구 이름은 `macro_fred`이며,
    기존 macro_regime.py의 `fred_get_series`는 silent-fail 버그였음
    (2026-04-11 Sprint 1 디버그에서 발견).
    """
    hub = _build_hub(fake_mcp, tmp_cache_dir)

    series = await hub.load_series(FredSeriesId.DGS10)

    # MCP 호출 검증
    assert len(fake_mcp.calls) == 1
    tool_name, args = fake_mcp.calls[0]
    assert tool_name == "macro_fred"
    assert args["series_id"] == "DGS10"
    assert "limit" in args

    # 반환 객체 검증
    assert series.meta.id == FredSeriesId.DGS10
    assert series.source == FredSource.MCP_NEXUS
    assert not series.data.empty
    assert "value" in series.data.columns


def test_registry_resolution_dgs10_returns_correct_meta() -> None:
    """FredSeriesRegistry.get_meta(DGS10)가 올바른 메타 반환."""
    registry = FredSeriesRegistry.load()
    meta = registry.get_meta(FredSeriesId.DGS10)

    assert meta.id == FredSeriesId.DGS10
    assert meta.fred_code == "DGS10"
    assert meta.label_ko == "미 10년물 국채금리"
    assert meta.freq == "daily"
    assert meta.transform == TransformType.NONE
    assert meta.category == SeriesCategory.RATES


@pytest.mark.asyncio
async def test_cache_hit_skips_mcp_call(
    fake_mcp: FakeMCPProvider, tmp_cache_dir: Path
) -> None:
    """두 번째 호출은 캐시 히트 → MCP 호출 1회만."""
    hub = _build_hub(fake_mcp, tmp_cache_dir)

    await hub.load_series(FredSeriesId.DGS10)
    await hub.load_series(FredSeriesId.DGS10)  # 캐시 히트

    # MCP는 1번만 호출되어야 함
    assert len(fake_mcp.calls) == 1


@pytest.mark.asyncio
async def test_yoy_transform_applied_when_registry_says_so(
    fake_mcp: FakeMCPProvider, tmp_cache_dir: Path
) -> None:
    """CPIAUCSL(YoY transform)은 load 후 pct_change_yoy 적용."""
    hub = _build_hub(fake_mcp, tmp_cache_dir)

    # 13개월 이상의 월간 데이터 준비 (YoY 변환에 필요)
    today = datetime.today().date()
    obs = []
    base = 280.0
    for i in range(24):
        d = today.replace(day=1) - timedelta(days=30 * (23 - i))
        obs.append({"date": d.isoformat(), "value": base * (1 + 0.002 * i)})
    fake_mcp.set_response("CPIAUCSL", {"data": obs})

    series = await hub.load_series(FredSeriesId.CPIAUCSL)
    assert series.meta.transform == TransformType.PCT_CHANGE_YOY
    # YoY 변환 후 값은 원시 280+ 값이 아닌 퍼센트 (약 2~10 범위)
    assert not series.data.empty
    assert series.data["value"].abs().max() < 50  # 퍼센트로 변환됐음


@pytest.mark.asyncio
async def test_mcp_error_raises_clear_runtime_error(
    fake_mcp: FakeMCPProvider, tmp_cache_dir: Path
) -> None:
    """MCP 호출 실패 시 명확한 RuntimeError."""
    hub = _build_hub(fake_mcp, tmp_cache_dir)
    fake_mcp.set_error(ConnectionError("Network down"))

    with pytest.raises(RuntimeError, match="macro_fred"):
        await hub.load_series(FredSeriesId.DGS10)


@pytest.mark.asyncio
async def test_load_many_returns_multiple_series(
    fake_mcp: FakeMCPProvider, tmp_cache_dir: Path
) -> None:
    """load_many()가 여러 시리즈를 한 번에 반환."""
    hub = _build_hub(fake_mcp, tmp_cache_dir)

    result = await hub.load_many(
        [FredSeriesId.DGS10, FredSeriesId.DGS2, FredSeriesId.VIXCLS]
    )

    assert len(result) == 3
    assert FredSeriesId.DGS10 in result
    assert FredSeriesId.DGS2 in result
    assert FredSeriesId.VIXCLS in result
    assert len(fake_mcp.calls) == 3


def test_detect_staleness_flags_stale_daily_series(
    fake_mcp: FakeMCPProvider,
    tmp_cache_dir: Path,
    sample_meta_dgs10,
) -> None:
    """10 영업일 지연된 daily 시리즈는 stale 판정."""
    hub = _build_hub(fake_mcp, tmp_cache_dir)

    # 10 영업일 전 데이터 (stale)
    past_date = date.today() - timedelta(days=20)
    df = pd.DataFrame(
        {"value": [4.12, 4.13]},
        index=pd.DatetimeIndex(
            [past_date - timedelta(days=1), past_date], name="date"
        ),
    )
    series = FredSeries(
        meta=sample_meta_dgs10,
        data=df,
        fetched_at=datetime.now(),
        source=FredSource.MCP_NEXUS,
    )

    report = hub.detect_staleness(series)
    assert isinstance(report, StalenessReport)
    assert report.is_stale is True
    assert report.business_days_stale > 5


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache(
    fake_mcp: FakeMCPProvider, tmp_cache_dir: Path
) -> None:
    """force_refresh=True 시 캐시 무시하고 MCP 재호출."""
    hub = _build_hub(fake_mcp, tmp_cache_dir)

    await hub.load_series(FredSeriesId.DGS10)
    await hub.load_series(FredSeriesId.DGS10, force_refresh=True)

    # 두 번 모두 MCP 호출되어야 함
    assert len(fake_mcp.calls) == 2


def test_parse_fred_mcp_response_handles_various_formats() -> None:
    """_parse_fred_mcp_response는 data/result/observations 키 모두 지원."""
    # Format 1: data 키
    r1 = {"data": [{"date": "2024-01-01", "value": 4.12}]}
    df1 = _parse_fred_mcp_response(r1)
    assert len(df1) == 1
    assert df1["value"].iloc[0] == 4.12

    # Format 2: observations 키
    r2 = {"observations": [{"date": "2024-01-01", "value": "4.15"}]}
    df2 = _parse_fred_mcp_response(r2)
    assert len(df2) == 1
    assert df2["value"].iloc[0] == 4.15

    # Format 3: FRED 결측 마커 "." 건너뛰기
    r3 = {
        "data": [
            {"date": "2024-01-01", "value": "."},
            {"date": "2024-01-02", "value": 4.20},
        ]
    }
    df3 = _parse_fred_mcp_response(r3)
    assert len(df3) == 1
    assert df3["value"].iloc[0] == 4.20


def test_apply_transform_none_returns_original() -> None:
    """transform=NONE은 변환 없이 반환."""
    df = pd.DataFrame({"value": [1.0, 2.0, 3.0]})
    result = _apply_transform(df, TransformType.NONE)
    assert result["value"].tolist() == [1.0, 2.0, 3.0]
