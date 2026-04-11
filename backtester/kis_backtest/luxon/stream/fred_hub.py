"""
Luxon Terminal — FREDHub (Sprint 1 A1)

Nexus MCP `fred_get_series` 도구 기반 FRED 시리즈 로더.
캐시 통합 + staleness 감지 + 변환(YoY 등) 적용.

설계 원칙 (찬희 지시 2026-04-11, MCP 우선):
    - `MCPDataProvider._call_vps_tool("fred_get_series", ...)` 호출
    - fredapi 직접 통합 금지 (MCP 398도구가 주 경로)
    - Parquet 캐시로 네트워크 비용 절감
    - 실데이터 절대 원칙 (목업 금지)

기존 자산 재사용:
    - `kis_backtest.portfolio.mcp_data_provider.MCPDataProvider` (100%)
    - `kis_backtest.portfolio.macro_regime._FRED_SERIES_MAP` 호출 패턴 차용
    - `kis_backtest.luxon.stream.schema.*` (SSOT 타입)
    - `kis_backtest.luxon.stream.fred_cache.FREDCache` (A3)

사용 예:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
    from kis_backtest.luxon.stream.fred_hub import FREDHub

    mcp = MCPDataProvider()
    hub = FREDHub(mcp=mcp)
    all_series = await hub.load_all()
    report = hub.detect_staleness(all_series[FredSeriesId.DGS10])
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, TYPE_CHECKING

import pandas as pd
import yaml

from kis_backtest.luxon.stream.fred_cache import FREDCache
from kis_backtest.luxon.stream.schema import (
    FredSeries,
    FredSeriesId,
    FredSeriesMeta,
    FredSource,
    SeriesCategory,
    StalenessReport,
    TransformType,
)

if TYPE_CHECKING:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

logger = logging.getLogger(__name__)

# 기본 레지스트리 경로
_DEFAULT_REGISTRY_PATH = (
    Path(__file__).parent / "series_registry.yaml"
)

# 업무일/달력일 결측 임계치 (카테고리별)
_STALENESS_THRESHOLDS_DAYS: dict[str, int] = {
    "daily": 5,      # 영업일 기준 5일
    "monthly": 45,   # 월간 지표는 45일
    "quarterly": 100,
}


class FredSeriesRegistry:
    """series_registry.yaml 로더 + 조회기.

    10개 FRED 시리즈 메타데이터 SSOT. 변경은 A5(Schema Guard)만.
    """

    def __init__(self, metas: list[FredSeriesMeta]) -> None:
        self._metas: dict[FredSeriesId, FredSeriesMeta] = {
            m.id: m for m in metas
        }

    @classmethod
    def load(cls, yaml_path: Path | None = None) -> "FredSeriesRegistry":
        """YAML에서 레지스트리 로드.

        Args:
            yaml_path: 레지스트리 YAML 경로. None이면 기본 경로 사용.
        """
        path = yaml_path or _DEFAULT_REGISTRY_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"FRED 시리즈 레지스트리 미존재: {path}"
            )
        with path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f)

        raw_series = doc.get("series", [])
        metas: list[FredSeriesMeta] = []
        for entry in raw_series:
            try:
                meta = FredSeriesMeta(
                    id=FredSeriesId(entry["id"]),
                    fred_code=entry["fred_code"],
                    label_ko=entry["label_ko"],
                    unit=entry["unit"],
                    freq=entry["freq"],
                    transform=TransformType(entry["transform"]),
                    category=SeriesCategory(entry["category"]),
                )
                metas.append(meta)
            except (KeyError, ValueError) as e:
                logger.error(
                    "series_registry.yaml 엔트리 파싱 실패: %s (%s)",
                    entry,
                    e,
                )
                raise
        logger.info("FredSeriesRegistry 로드 완료: %d 시리즈", len(metas))
        return cls(metas)

    def get_meta(self, series_id: FredSeriesId) -> FredSeriesMeta:
        if series_id not in self._metas:
            raise KeyError(
                f"시리즈 {series_id.value}가 레지스트리에 없음"
            )
        return self._metas[series_id]

    def all_series(self) -> list[FredSeriesMeta]:
        """전체 시리즈 메타 목록 (순서 보장: FredSeriesId Enum 순)."""
        return [
            self._metas[sid] for sid in FredSeriesId if sid in self._metas
        ]


def _parse_fred_mcp_response(result: dict[str, Any]) -> pd.DataFrame:
    """Nexus MCP `fred_get_series` 응답 → DataFrame.

    기대 구조:
        {"data": [{"date": "2024-01-01", "value": 4.12}, ...]}
        또는 {"result": [...]}
        또는 {"observations": [...]}
        또는 직접 list

    반환:
        DataFrame(index=DatetimeIndex, columns=["value"])
        빈 응답 시 빈 DataFrame (단, FREDHub가 예외 발생)
    """
    if not isinstance(result, dict):
        return pd.DataFrame(columns=["value"])

    data = (
        result.get("data")
        or result.get("result")
        or result.get("observations")
        or result
    )
    if not isinstance(data, list):
        return pd.DataFrame(columns=["value"])

    rows: list[tuple[pd.Timestamp, float]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        date_val = (
            item.get("date")
            or item.get("DATE")
            or item.get("observation_date")
        )
        if not date_val:
            continue
        raw_value: Any = None
        for key in ("value", "DATA_VALUE", "data_value"):
            if key in item:
                raw_value = item[key]
                break
        if raw_value in (None, "", "."):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        try:
            ts = pd.Timestamp(date_val)
        except (TypeError, ValueError):
            continue
        rows.append((ts, value))

    if not rows:
        return pd.DataFrame(columns=["value"])

    df = pd.DataFrame(rows, columns=["date", "value"]).set_index("date")
    df.index = pd.DatetimeIndex(df.index, name="date")
    df = df.sort_index()
    # 중복 제거 (같은 날짜 여러 번 등장 시 마지막 값)
    df = df[~df.index.duplicated(keep="last")]
    return df


def _apply_transform(df: pd.DataFrame, transform: TransformType) -> pd.DataFrame:
    """메타의 transform 필드에 따라 시리즈 변환."""
    if df.empty:
        return df
    if transform == TransformType.NONE:
        return df
    if transform == TransformType.PCT_CHANGE_YOY:
        # 월간 지표 기준 12개월 전 대비 % (최소 13개 필요)
        if len(df) < 13:
            logger.warning(
                "YoY 변환 불가: 시리즈 길이 %d < 13", len(df)
            )
            return df
        transformed = df["value"].pct_change(periods=12) * 100
        return pd.DataFrame({"value": transformed.dropna()})
    if transform == TransformType.DIFF:
        return pd.DataFrame({"value": df["value"].diff().dropna()})
    return df


def _business_days_between(start: date, end: date) -> int:
    """영업일(월~금) 개수. 공휴일 미고려."""
    if start >= end:
        return 0
    count = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return count


class FREDHub:
    """FRED 시리즈 허브 (MCP 우선, 캐시 통합).

    주 경로:
        load_series(id) → cache.get() (hit 시 반환) → MCP 호출 → parse → transform → cache.put() → return

    환경 변수:
        NEXUS_MCP_TOKEN: MCPDataProvider가 자동 로드
        LUXON_CACHE_DIR / LUXON_FRED_TTL_HOURS: 캐시 설정
    """

    def __init__(
        self,
        mcp: "MCPDataProvider",
        cache: FREDCache | None = None,
        registry: FredSeriesRegistry | None = None,
    ) -> None:
        self._mcp = mcp
        self._cache = cache or FREDCache()
        self._registry = registry or FredSeriesRegistry.load()

    @property
    def cache(self) -> FREDCache:
        return self._cache

    @property
    def registry(self) -> FredSeriesRegistry:
        return self._registry

    async def load_series(
        self,
        series_id: FredSeriesId,
        force_refresh: bool = False,
        limit: int = 500,
    ) -> FredSeries:
        """단일 FRED 시리즈 로드.

        Args:
            series_id: 로드할 시리즈 ID.
            force_refresh: True면 캐시 무시하고 MCP 재호출.
            limit: MCP에 요청할 최대 관측 수 (월간 500개 ≈ 40년).
        """
        meta = self._registry.get_meta(series_id)

        # 1. 캐시 확인 (force_refresh 아니면)
        if not force_refresh:
            cached = self._cache.get(series_id)
            if cached is not None:
                entry = self._cache_entry_from_series(cached)
                if not self._cache.is_expired(entry):
                    logger.debug(
                        "FRED 캐시 hit: %s (fetched=%s)",
                        series_id.value,
                        cached.fetched_at.isoformat(),
                    )
                    return cached

        # 2. MCP 호출 (Nexus MCP 398도구 중 `macro_fred`가 실제 FRED 도구)
        # 기존 macro_regime.py가 잘못된 이름 `fred_get_series`를 사용하던 버그
        # 발견 (2026-04-11, Luxon Sprint 1 디버그). 올바른 이름으로 교체.
        logger.info(
            "MCP macro_fred 호출: %s (fred_code=%s, limit=%d)",
            series_id.value,
            meta.fred_code,
            limit,
        )
        try:
            result = await self._mcp._call_vps_tool(
                "macro_fred",
                {"series_id": meta.fred_code, "limit": limit},
            )
        except Exception as e:
            raise RuntimeError(
                f"MCP macro_fred 호출 실패 [{series_id.value}]: {e}"
            ) from e

        # MCP 서버가 success=True여도 data가 에러 문자열일 수 있음
        if isinstance(result, dict) and isinstance(result.get("data"), str):
            raise RuntimeError(
                f"MCP macro_fred 서버 에러 [{series_id.value}]: "
                f"{result['data']}"
            )

        # 3. 파싱
        df = _parse_fred_mcp_response(result)
        if df.empty:
            raise RuntimeError(
                f"MCP macro_fred 빈 응답 [{series_id.value}] "
                f"(fred_code={meta.fred_code}) "
                f"— 실데이터 절대 원칙 위반 방지 (목업 생성 금지)"
            )

        # 4. 변환 적용
        df = _apply_transform(df, meta.transform)
        if df.empty:
            raise RuntimeError(
                f"변환 후 빈 데이터 [{series_id.value}] "
                f"(transform={meta.transform.value})"
            )

        # 5. FredSeries 생성
        series = FredSeries(
            meta=meta,
            data=df,
            fetched_at=datetime.now(),
            source=FredSource.MCP_NEXUS,
        )

        # 6. 캐시 저장
        self._cache.put(series)
        return series

    async def load_many(
        self,
        series_ids: list[FredSeriesId] | None = None,
        force_refresh: bool = False,
    ) -> dict[FredSeriesId, FredSeries]:
        """여러 시리즈 순차 로드 (실패는 개별 로그, 계속 진행)."""
        targets = series_ids or [m.id for m in self._registry.all_series()]
        results: dict[FredSeriesId, FredSeries] = {}
        for sid in targets:
            try:
                results[sid] = await self.load_series(
                    sid, force_refresh=force_refresh
                )
            except Exception as e:
                logger.warning("시리즈 로드 실패: %s (%s)", sid.value, e)
        return results

    async def load_all(
        self, force_refresh: bool = False
    ) -> dict[FredSeriesId, FredSeries]:
        """레지스트리 전체 (10개) 시리즈 로드."""
        return await self.load_many(None, force_refresh=force_refresh)

    def detect_staleness(self, series: FredSeries) -> StalenessReport:
        """시리즈 마지막 관측일이 임계치를 넘었는지 감지.

        daily → 5 영업일
        monthly → 45 달력일
        quarterly → 100 달력일
        """
        meta = series.meta
        threshold_days = _STALENESS_THRESHOLDS_DAYS.get(meta.freq, 5)
        today = date.today()
        last = series.last_observation

        if meta.freq == "daily":
            stale_count = _business_days_between(last, today)
        else:
            stale_count = (today - last).days

        return StalenessReport(
            series_id=meta.id,
            last_observation=last,
            business_days_stale=stale_count,
            is_stale=stale_count > threshold_days,
            threshold_business_days=threshold_days,
        )

    def _cache_entry_from_series(self, series: FredSeries):
        """FredSeries에서 CacheEntry 재구성 (TTL 체크용)."""
        from kis_backtest.luxon.stream.schema import CacheEntry

        return CacheEntry(
            series_id=series.meta.id,
            cache_path=self._cache._cache_path(series.meta.id),
            cached_at=series.fetched_at,
            ttl_hours=self._cache.ttl_hours,
        )


__all__ = [
    "FREDHub",
    "FredSeriesRegistry",
    "_parse_fred_mcp_response",
    "_apply_transform",
]
