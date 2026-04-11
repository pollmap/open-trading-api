"""Luxon Sprint 1 pytest fixtures.

Fake MCP Provider (unit test용) + 공통 샘플 데이터.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from kis_backtest.luxon.stream.schema import (
    FredSeries,
    FredSeriesId,
    FredSeriesMeta,
    FredSource,
    SeriesCategory,
    TransformType,
)


class FakeMCPProvider:
    """단위 테스트용 MCPDataProvider 대체 (실제 MCP 서버 불필요).

    `_call_vps_tool(tool_name, arguments)` 호출을 가로채고
    미리 설정된 응답을 반환한다.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._responses: dict[str, Any] = {}
        self._should_raise: Exception | None = None

    def set_response(self, series_id: str, response: dict) -> None:
        """특정 FRED series_id(fred_code)에 대한 응답 등록."""
        self._responses[series_id] = response

    def set_error(self, error: Exception) -> None:
        """다음 호출 시 발생시킬 예외."""
        self._should_raise = error

    async def _call_vps_tool(
        self, tool_name: str, arguments: dict | None = None
    ) -> dict:
        """MCPDataProvider._call_vps_tool 시그니처 미러."""
        args = arguments or {}
        self.calls.append((tool_name, dict(args)))

        if self._should_raise is not None:
            err = self._should_raise
            self._should_raise = None
            raise err

        series_id = args.get("series_id", "")
        if series_id in self._responses:
            return self._responses[series_id]

        # 기본 응답: 샘플 데이터 생성 (daily 30일)
        return _generate_sample_fred_response(series_id, 30)


def _generate_sample_fred_response(fred_code: str, days: int) -> dict:
    """테스트용 샘플 FRED 응답 생성 (실데이터 형식 모방)."""
    today = datetime.today().date()
    observations = []
    base_value = {"DGS10": 4.12, "DGS2": 4.50, "T10Y2Y": -0.38}.get(
        fred_code, 100.0
    )
    for i in range(days):
        obs_date = today - timedelta(days=days - i - 1)
        # 주말 제외 (daily 시리즈 시뮬레이션)
        if obs_date.weekday() >= 5:
            continue
        value = base_value + (i * 0.01)
        observations.append({"date": obs_date.isoformat(), "value": value})
    return {"data": observations, "series_id": fred_code}


@pytest.fixture
def fake_mcp() -> FakeMCPProvider:
    """기본 FakeMCPProvider fixture."""
    return FakeMCPProvider()


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    """임시 캐시 디렉토리."""
    cache_dir = tmp_path / "luxon_fred_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@pytest.fixture
def sample_meta_dgs10() -> FredSeriesMeta:
    """DGS10 시리즈 메타 샘플."""
    return FredSeriesMeta(
        id=FredSeriesId.DGS10,
        fred_code="DGS10",
        label_ko="미 10년물 국채금리",
        unit="percent",
        freq="daily",
        transform=TransformType.NONE,
        category=SeriesCategory.RATES,
    )


@pytest.fixture
def sample_meta_cpiaucsl() -> FredSeriesMeta:
    """CPIAUCSL 시리즈 메타 샘플 (YoY 변환 대상)."""
    return FredSeriesMeta(
        id=FredSeriesId.CPIAUCSL,
        fred_code="CPIAUCSL",
        label_ko="소비자물가지수 (YoY)",
        unit="percent",
        freq="monthly",
        transform=TransformType.PCT_CHANGE_YOY,
        category=SeriesCategory.INFLATION,
    )


@pytest.fixture
def sample_series_dgs10(sample_meta_dgs10: FredSeriesMeta) -> FredSeries:
    """FredSeries 샘플 (DGS10, 일별 30일)."""
    today = datetime.today().date()
    dates = pd.date_range(end=today, periods=30, freq="B")
    df = pd.DataFrame(
        {"value": [4.10 + i * 0.01 for i in range(len(dates))]},
        index=dates,
    )
    df.index.name = "date"
    return FredSeries(
        meta=sample_meta_dgs10,
        data=df,
        fetched_at=datetime.now(),
        source=FredSource.MCP_NEXUS,
    )
