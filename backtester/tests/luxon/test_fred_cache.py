"""Sprint 1 — FREDCache (A3) 단위 테스트.

Parquet 캐시 라운드트립 + TTL + 삭제 검증.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kis_backtest.luxon.stream.fred_cache import FREDCache
from kis_backtest.luxon.stream.schema import FredSeries, FredSeriesId


def test_put_and_get_roundtrip_pickle(
    tmp_cache_dir: Path, sample_series_dgs10: FredSeries
) -> None:
    """put → get 라운드트립: 동일한 FredSeries 반환."""
    cache = FREDCache(cache_dir=tmp_cache_dir, ttl_hours=6)
    entry = cache.put(sample_series_dgs10)

    assert entry.series_id == FredSeriesId.DGS10
    assert entry.cache_path.exists()
    assert entry.cache_path.suffix == ".pkl"

    loaded = cache.get(FredSeriesId.DGS10)
    assert loaded is not None
    assert loaded.meta.id == FredSeriesId.DGS10
    assert loaded.meta.label_ko == "미 10년물 국채금리"
    assert len(loaded.data) == len(sample_series_dgs10.data)
    # 값 일치 (소수점 오차 허용)
    assert abs(loaded.data["value"].iloc[-1] - sample_series_dgs10.data["value"].iloc[-1]) < 1e-6


def test_cache_expiration_after_ttl(
    tmp_cache_dir: Path, sample_series_dgs10: FredSeries
) -> None:
    """TTL 경과 후 is_expired True."""
    cache = FREDCache(cache_dir=tmp_cache_dir, ttl_hours=6)
    entry = cache.put(sample_series_dgs10)

    # 방금 저장 → 만료 아님
    assert not cache.is_expired(entry)

    # cached_at을 과거로 조작한 entry
    from kis_backtest.luxon.stream.schema import CacheEntry

    expired_entry = CacheEntry(
        series_id=entry.series_id,
        cache_path=entry.cache_path,
        cached_at=datetime.now() - timedelta(hours=7),
        ttl_hours=6,
    )
    assert cache.is_expired(expired_entry)


def test_clear_removes_specific_series(
    tmp_cache_dir: Path, sample_series_dgs10: FredSeries
) -> None:
    """clear(series_id)는 해당 시리즈만 삭제."""
    cache = FREDCache(cache_dir=tmp_cache_dir, ttl_hours=6)
    cache.put(sample_series_dgs10)
    assert cache.get(FredSeriesId.DGS10) is not None

    deleted = cache.clear(FredSeriesId.DGS10)
    assert deleted == 1
    assert cache.get(FredSeriesId.DGS10) is None


def test_empty_cache_returns_none(tmp_cache_dir: Path) -> None:
    """존재하지 않는 시리즈 요청 시 None."""
    cache = FREDCache(cache_dir=tmp_cache_dir, ttl_hours=6)
    assert cache.get(FredSeriesId.DGS10) is None
    assert cache.get(FredSeriesId.VIXCLS) is None
    stats = cache.stats()
    assert stats["cached_series"] == 0
