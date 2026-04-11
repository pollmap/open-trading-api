"""
Luxon Terminal — FRED Pickle Cache (Sprint 1 A3)

Python pickle 기반 로컬 캐시 + TTL 관리 + staleness 감지.
MCP 호출을 줄여 네트워크 비용/지연 절감 + 오프라인 재시작 시 빠른 로드.

왜 pickle인가:
    - 의존성 0 (Python 표준 라이브러리)
    - pandas DataFrame + 메타데이터 전부 보존 (attrs, dtype)
    - 빠름 (parquet 대비 단일 프로세스 기준 비슷하거나 빠름)
    - Luxon은 Python 전용 → 다른 언어 호환성 불필요

설계 원칙 (CLAUDE.md 준수):
    1. 불변성 — CacheEntry frozen dataclass
    2. 명시적 실패 — 캐시 손상 시 None 반환 + 경고 로그
    3. 실데이터 보존 — 캐시 내용은 실제 MCP에서 받은 것만 저장

경로 규약:
    기본: ~/.luxon/cache/fred/{series_id}.pkl
    환경 변수 override: LUXON_CACHE_DIR
    TTL 기본: 6시간 (환경 변수 override: LUXON_FRED_TTL_HOURS)
"""
from __future__ import annotations

import logging
import os
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from kis_backtest.luxon.stream.schema import (
    CacheEntry,
    FredSeries,
    FredSeriesId,
    FredSeriesMeta,
    FredSource,
    SeriesCategory,
    TransformType,
)

logger = logging.getLogger(__name__)

# 캐시 번들 포맷 버전 (향후 호환성)
_CACHE_FORMAT_VERSION = 1


def _default_cache_dir() -> Path:
    """캐시 기본 디렉토리 (env override 지원)."""
    env_dir = os.environ.get("LUXON_CACHE_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return Path.home() / ".luxon" / "cache" / "fred"


def _default_ttl_hours() -> int:
    """캐시 TTL 기본값 (env override 지원)."""
    env_ttl = os.environ.get("LUXON_FRED_TTL_HOURS")
    if env_ttl:
        try:
            return int(env_ttl)
        except ValueError:
            logger.warning(
                "LUXON_FRED_TTL_HOURS=%s 파싱 실패, 기본값 6 사용", env_ttl
            )
    return 6


class FREDCache:
    """FRED 시리즈 pickle 캐시.

    캐시 레이아웃:
        {cache_dir}/{series_id}.pkl   — pickle 번들

    번들 구조:
        {
            "version": 1,
            "meta": {id, fred_code, label_ko, unit, freq, transform, category},
            "data": DataFrame(columns=["value"], index=DatetimeIndex),
            "fetched_at": isoformat str,
            "source": FredSource value str,
        }

    사용 예:
        cache = FREDCache()
        cached = cache.get(FredSeriesId.DGS10)
        if cached is None or cache.is_expired(CacheEntry(...)):
            series = await fetch_from_mcp(...)
            cache.put(series)
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        ttl_hours: int | None = None,
    ) -> None:
        self._cache_dir: Path = (cache_dir or _default_cache_dir()).expanduser()
        self._ttl_hours: int = (
            ttl_hours if ttl_hours is not None else _default_ttl_hours()
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(
            "FREDCache 초기화: dir=%s, ttl=%dh", self._cache_dir, self._ttl_hours
        )

    @property
    def cache_dir(self) -> Path:
        """캐시 디렉토리 경로 (읽기 전용)."""
        return self._cache_dir

    @property
    def ttl_hours(self) -> int:
        """캐시 TTL 시간 (읽기 전용)."""
        return self._ttl_hours

    def _cache_path(self, series_id: FredSeriesId) -> Path:
        """특정 시리즈의 캐시 파일 경로."""
        return self._cache_dir / f"{series_id.value}.pkl"

    def get(self, series_id: FredSeriesId) -> FredSeries | None:
        """캐시에서 시리즈 로드. 없거나 손상 시 None.

        TTL 검사는 호출자가 is_expired()로 수행 (get은 순수 로드).
        """
        path = self._cache_path(series_id)
        if not path.exists():
            return None
        try:
            with path.open("rb") as f:
                bundle: dict[str, Any] = pickle.load(f)

            # 포맷 검증
            if not isinstance(bundle, dict) or bundle.get("version") != _CACHE_FORMAT_VERSION:
                logger.warning(
                    "FREDCache 포맷 불일치: %s (버전=%s, 기대=%d)",
                    series_id.value,
                    bundle.get("version") if isinstance(bundle, dict) else "?",
                    _CACHE_FORMAT_VERSION,
                )
                return None

            meta_dict = bundle["meta"]
            meta = FredSeriesMeta(
                id=FredSeriesId(meta_dict["id"]),
                fred_code=meta_dict["fred_code"],
                label_ko=meta_dict["label_ko"],
                unit=meta_dict["unit"],
                freq=meta_dict["freq"],
                transform=TransformType(meta_dict["transform"]),
                category=SeriesCategory(meta_dict["category"]),
            )
            return FredSeries(
                meta=meta,
                data=bundle["data"],
                fetched_at=datetime.fromisoformat(bundle["fetched_at"]),
                source=FredSource(bundle["source"]),
            )
        except Exception as e:
            logger.warning(
                "FREDCache 로드 실패: %s (path=%s): %s",
                series_id.value,
                path,
                e,
            )
            return None

    def put(self, series: FredSeries) -> CacheEntry:
        """시리즈를 pickle로 저장.

        빈 DataFrame이나 잘못된 인덱스는 FredSeries.__post_init__에서 거부됨.
        """
        path = self._cache_path(series.meta.id)

        bundle: dict[str, Any] = {
            "version": _CACHE_FORMAT_VERSION,
            "meta": {
                "id": series.meta.id.value,
                "fred_code": series.meta.fred_code,
                "label_ko": series.meta.label_ko,
                "unit": series.meta.unit,
                "freq": series.meta.freq,
                "transform": series.meta.transform.value,
                "category": series.meta.category.value,
            },
            "data": series.data[["value"]].copy(),
            "fetched_at": series.fetched_at.isoformat(),
            "source": series.source.value,
        }

        with path.open("wb") as f:
            pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)

        logger.debug(
            "FREDCache 저장: %s → %s (rows=%d)",
            series.meta.id.value,
            path,
            len(series.data),
        )

        return CacheEntry(
            series_id=series.meta.id,
            cache_path=path,
            cached_at=series.fetched_at,
            ttl_hours=self._ttl_hours,
        )

    def is_expired(self, entry: CacheEntry) -> bool:
        """CacheEntry가 TTL을 초과했는지 검사.

        schema.CacheEntry.is_expired()를 호출 (로직 중복 방지).
        """
        return entry.is_expired()

    def clear(self, series_id: FredSeriesId | None = None) -> int:
        """캐시 삭제.

        Args:
            series_id: 특정 시리즈만 삭제. None이면 전체.

        Returns:
            삭제된 파일 수.
        """
        if series_id is not None:
            path = self._cache_path(series_id)
            if path.exists():
                path.unlink()
                return 1
            return 0

        count = 0
        for cache_file in self._cache_dir.glob("*.pkl"):
            cache_file.unlink()
            count += 1
        logger.info("FREDCache 전체 삭제: %d개 파일", count)
        return count

    def stats(self) -> dict[str, int | str]:
        """캐시 현황 요약."""
        files = list(self._cache_dir.glob("*.pkl"))
        total_size = sum(f.stat().st_size for f in files)
        return {
            "cache_dir": str(self._cache_dir),
            "ttl_hours": self._ttl_hours,
            "cached_series": len(files),
            "total_bytes": total_size,
        }


__all__ = ["FREDCache"]
