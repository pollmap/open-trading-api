"""Sprint 3 — TickVault 단위 테스트.

설계 계약 (naming_registry_sprint3.md):
    - pickle 기반 일별 파일 (pyarrow 의존 0)
    - 경로 규약: {root}/{exchange}/{symbol}/{YYYY-MM-DD}.pkl
    - env override: LUXON_TICK_DATA_DIR / LUXON_TICK_RETENTION_DAYS / LUXON_TICK_FLUSH_INTERVAL
    - 실데이터 절대 원칙: TickPoint는 빈 데이터/0 가격 거부

테스트 전략:
    - 모든 I/O는 tmp_path에 격리
    - 시간은 datetime.combine으로 명시적 생성 (time.sleep 금지)
    - 파일 수는 glob으로 검증 (pickle 로드 없이)
"""
from __future__ import annotations

import pickle
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from kis_backtest.luxon.stream.schema import Exchange, TickPoint
from kis_backtest.luxon.stream.tick_vault import TickVault


# ======================================================================
# TickPoint 검증 (SSOT 불변식)
# ======================================================================


def test_tickpoint_rejects_zero_price() -> None:
    """실데이터 절대 원칙: last<=0 거부."""
    with pytest.raises(ValueError, match="last"):
        TickPoint(
            timestamp=datetime(2026, 4, 11, 9, 0, 0),
            symbol="005930",
            exchange=Exchange.KIS,
            last=0.0,
        )


def test_tickpoint_rejects_empty_symbol() -> None:
    """빈 symbol은 목업 시그널 → 거부."""
    with pytest.raises(ValueError, match="symbol"):
        TickPoint(
            timestamp=datetime(2026, 4, 11, 9, 0, 0),
            symbol="",
            exchange=Exchange.UPBIT,
            last=100.0,
        )


def test_tickpoint_rejects_non_datetime() -> None:
    """timestamp는 반드시 datetime."""
    with pytest.raises(TypeError, match="datetime"):
        TickPoint(
            timestamp="2026-04-11",  # type: ignore[arg-type]
            symbol="005930",
            exchange=Exchange.KIS,
            last=75_000.0,
        )


def test_tickpoint_rejects_negative_volume() -> None:
    """volume<0 거부 (None은 허용)."""
    with pytest.raises(ValueError, match="volume"):
        TickPoint(
            timestamp=datetime(2026, 4, 11, 9, 0, 0),
            symbol="005930",
            exchange=Exchange.KIS,
            last=75_000.0,
            volume=-1.0,
        )


# ======================================================================
# TickVault 기본 기능
# ======================================================================


def _make_tick(
    sec: int,
    symbol: str = "005930",
    exchange: Exchange = Exchange.KIS,
    price: float = 75_000.0,
    day: date | None = None,
) -> TickPoint:
    """테스트용 TickPoint 헬퍼."""
    trade_day = day or date(2026, 4, 11)
    return TickPoint(
        timestamp=datetime.combine(trade_day, time(9, 0, sec % 60)),
        symbol=symbol,
        exchange=exchange,
        last=price + sec,
        bid=price + sec - 10,
        ask=price + sec + 10,
        volume=float(10 + sec),
    )


def test_append_and_load_roundtrip(tmp_path: Path) -> None:
    """append 3건 → flush_all → load_day 3건 일치."""
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)

    ticks = [_make_tick(i, day=trade_day) for i in range(3)]
    for t in ticks:
        vault.append(t)

    metas = vault.flush_all()
    assert len(metas) == 1
    assert metas[0].tick_count == 3

    loaded = vault.load_day(Exchange.KIS, "005930", trade_day)
    assert len(loaded) == 3
    assert loaded[0].last == 75_000.0
    assert loaded[-1].last == 75_002.0


def test_flush_creates_file_at_expected_path(tmp_path: Path) -> None:
    """경로 규약: {root}/{exchange}/{symbol}/{YYYY-MM-DD}.pkl"""
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)

    vault.append(_make_tick(0, day=trade_day))
    vault.flush_all()

    expected = tmp_path / "kis" / "005930" / "2026-04-11.pkl"
    assert expected.exists()
    assert expected.stat().st_size > 0


def test_multiple_symbols_isolated(tmp_path: Path) -> None:
    """서로 다른 symbol은 서로 다른 파일에 저장."""
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)

    vault.append(_make_tick(1, symbol="005930", day=trade_day))
    vault.append(_make_tick(2, symbol="000660", day=trade_day))
    vault.flush_all()

    samsung = vault.load_day(Exchange.KIS, "005930", trade_day)
    sk_hynix = vault.load_day(Exchange.KIS, "000660", trade_day)

    assert len(samsung) == 1
    assert len(sk_hynix) == 1
    assert samsung[0].symbol == "005930"
    assert sk_hynix[0].symbol == "000660"


def test_different_exchanges_isolated(tmp_path: Path) -> None:
    """KIS와 Upbit는 별도 디렉토리."""
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)

    vault.append(_make_tick(1, symbol="005930", exchange=Exchange.KIS, day=trade_day))
    vault.append(
        _make_tick(
            2,
            symbol="KRW-BTC",
            exchange=Exchange.UPBIT,
            price=95_000_000.0,
            day=trade_day,
        )
    )
    vault.flush_all()

    assert (tmp_path / "kis" / "005930" / "2026-04-11.pkl").exists()
    assert (tmp_path / "upbit" / "KRW-BTC" / "2026-04-11.pkl").exists()


def test_append_beyond_flush_interval_auto_flushes(tmp_path: Path) -> None:
    """flush_interval 도달 시 자동 flush → 파일 즉시 생성."""
    vault = TickVault(root_dir=tmp_path, flush_interval=3)
    trade_day = date(2026, 4, 11)

    vault.append(_make_tick(0, day=trade_day))
    vault.append(_make_tick(1, day=trade_day))
    # flush_interval 미도달
    expected = tmp_path / "kis" / "005930" / "2026-04-11.pkl"
    assert not expected.exists()

    vault.append(_make_tick(2, day=trade_day))
    # 이제 auto flush
    assert expected.exists()


def test_load_day_returns_empty_when_missing(tmp_path: Path) -> None:
    """없는 파일 → 빈 리스트 (예외 X)."""
    vault = TickVault(root_dir=tmp_path)
    loaded = vault.load_day(Exchange.KIS, "999999", date(2000, 1, 1))
    assert loaded == []


def test_describe_returns_meta_with_counts(tmp_path: Path) -> None:
    """describe는 tick_count, first/last timestamp, 파일 크기 반환."""
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)

    vault.append(_make_tick(0, day=trade_day))
    vault.append(_make_tick(30, day=trade_day))
    vault.flush_all()

    meta = vault.describe(Exchange.KIS, "005930", trade_day)
    assert meta is not None
    assert meta.tick_count == 2
    assert meta.first_timestamp is not None
    assert meta.last_timestamp is not None
    assert meta.first_timestamp < meta.last_timestamp
    assert meta.bytes_on_disk > 0


def test_list_days_sorted(tmp_path: Path) -> None:
    """list_days는 오름차순."""
    vault = TickVault(root_dir=tmp_path, flush_interval=100)

    for day in [date(2026, 4, 9), date(2026, 4, 11), date(2026, 4, 10)]:
        vault.append(_make_tick(0, day=day))
    vault.flush_all()

    days = vault.list_days(Exchange.KIS, "005930")
    assert days == [date(2026, 4, 9), date(2026, 4, 10), date(2026, 4, 11)]


def test_prune_deletes_old_files(tmp_path: Path) -> None:
    """prune(older_than_days=5)은 5일보다 오래된 파일만 삭제."""
    vault = TickVault(root_dir=tmp_path, flush_interval=100, retention_days=5)

    today = date.today()
    old_day = today - timedelta(days=100)
    recent_day = today - timedelta(days=2)

    vault.append(_make_tick(0, day=old_day))
    vault.append(_make_tick(0, day=recent_day))
    vault.flush_all()

    deleted = vault.prune()  # retention_days=5 사용
    assert deleted == 1

    # recent는 살아있음
    remaining = vault.list_days(Exchange.KIS, "005930")
    assert recent_day in remaining
    assert old_day not in remaining


def test_append_to_existing_file_merges(tmp_path: Path) -> None:
    """기존 파일에 flush → load + 새 틱 병합 (append 시맨틱)."""
    vault = TickVault(root_dir=tmp_path, flush_interval=2)
    trade_day = date(2026, 4, 11)

    vault.append(_make_tick(0, day=trade_day))
    vault.append(_make_tick(1, day=trade_day))  # auto flush
    vault.append(_make_tick(2, day=trade_day))
    vault.append(_make_tick(3, day=trade_day))  # auto flush, merge

    loaded = vault.load_day(Exchange.KIS, "005930", trade_day)
    assert len(loaded) == 4


def test_context_manager_flushes_on_exit(tmp_path: Path) -> None:
    """with 블록 종료 시 자동 flush."""
    trade_day = date(2026, 4, 11)
    expected = tmp_path / "kis" / "005930" / "2026-04-11.pkl"

    with TickVault(root_dir=tmp_path, flush_interval=100) as vault:
        vault.append(_make_tick(0, day=trade_day))
        assert not expected.exists()  # 아직 flush 전

    assert expected.exists()


def test_stats_reports_totals(tmp_path: Path) -> None:
    """stats는 총 파일 수/바이트/심볼 수 보고."""
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)

    vault.append(_make_tick(0, symbol="005930", day=trade_day))
    vault.append(_make_tick(0, symbol="000660", day=trade_day))
    vault.flush_all()

    stats = vault.stats()
    assert stats["total_files"] == 2
    assert stats["symbol_count"] == 2
    assert stats["total_bytes"] > 0
    assert stats["buffered_ticks"] == 0


# ======================================================================
# 손상 내성 (R11 재발 방지 체크리스트 반영)
# ======================================================================


def test_load_rejects_invalid_bundle_version(tmp_path: Path) -> None:
    """포맷 버전 불일치 파일 → 조용히 drop (경고 후 빈 리스트)."""
    vault = TickVault(root_dir=tmp_path)
    trade_day = date(2026, 4, 11)
    bad_path = tmp_path / "kis" / "005930" / "2026-04-11.pkl"
    bad_path.parent.mkdir(parents=True, exist_ok=True)

    # 버전 999로 저장된 손상 파일 시뮬레이션
    with bad_path.open("wb") as f:
        pickle.dump(
            {
                "version": 999,
                "exchange": "kis",
                "symbol": "005930",
                "day": trade_day.isoformat(),
                "ticks": [],
            },
            f,
        )

    loaded = vault.load_day(Exchange.KIS, "005930", trade_day)
    assert loaded == []


# ======================================================================
# 환경 변수 override
# ======================================================================


def test_env_override_root_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LUXON_TICK_DATA_DIR이 root_dir 기본값을 덮어씀."""
    override = tmp_path / "override_ticks"
    monkeypatch.setenv("LUXON_TICK_DATA_DIR", str(override))

    vault = TickVault()  # 인자 없이 생성 → env 사용
    assert vault.root_dir == override
    assert vault.root_dir.exists()


def test_env_override_retention_days(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """LUXON_TICK_RETENTION_DAYS 적용."""
    monkeypatch.setenv("LUXON_TICK_RETENTION_DAYS", "30")
    vault = TickVault(root_dir=tmp_path)
    assert vault.retention_days == 30
