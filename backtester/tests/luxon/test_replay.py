"""Sprint 3 — TickReplayer 단위 테스트.

speed/start_offset/limit 옵션 + 동기/비동기 재생 + 빈 파일 내성.

실행 시간 주의:
    - speed=1.0 테스트는 time.sleep 호출 → 틱 간 gap은 0.01초 이하로 제한
    - speed=-1 (무한속도)는 sleep 0 → 대부분의 테스트가 이 모드 사용
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from pathlib import Path

import pytest

from kis_backtest.luxon.stream.replay import TickReplayer
from kis_backtest.luxon.stream.schema import Exchange, ReplaySpec, TickPoint
from kis_backtest.luxon.stream.tick_vault import TickVault


def _make_tick(
    sec: int,
    day: date = date(2026, 4, 11),
    symbol: str = "005930",
    exchange: Exchange = Exchange.KIS,
) -> TickPoint:
    return TickPoint(
        timestamp=datetime.combine(day, time(9, 0, sec % 60)),
        symbol=symbol,
        exchange=exchange,
        last=75_000.0 + sec,
        bid=75_000.0 + sec - 10,
        ask=75_000.0 + sec + 10,
        volume=10.0 + sec,
    )


def _seed_vault(vault: TickVault, n: int, day: date) -> None:
    for i in range(n):
        vault.append(_make_tick(i, day=day))
    vault.flush_all()


# ======================================================================
# ReplaySpec 기본 검증
# ======================================================================


def test_replayspec_rejects_negative_offset() -> None:
    with pytest.raises(ValueError, match="start_offset"):
        ReplaySpec(start_offset=-1)


def test_replayspec_rejects_negative_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        ReplaySpec(limit=-5)


def test_replayspec_unlimited_speed_flag() -> None:
    assert ReplaySpec(speed=0).is_unlimited_speed is True
    assert ReplaySpec(speed=-1).is_unlimited_speed is True
    assert ReplaySpec(speed=1.0).is_unlimited_speed is False
    assert ReplaySpec(speed=2.5).is_unlimited_speed is False


# ======================================================================
# 동기 재생 — 순서/offset/limit
# ======================================================================


def test_replay_returns_all_ticks_in_timestamp_order(tmp_path: Path) -> None:
    """기본 speed=1이지만 gap이 1초 → 실제 sleep 최소화. 순서만 검증."""
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)
    _seed_vault(vault, 5, trade_day)

    replayer = TickReplayer(vault)
    result = replayer.replay_list(
        Exchange.KIS, "005930", trade_day, ReplaySpec(speed=-1)
    )

    assert len(result) == 5
    assert [t.last for t in result] == [75_000.0 + i for i in range(5)]


def test_replay_respects_start_offset(tmp_path: Path) -> None:
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)
    _seed_vault(vault, 10, trade_day)

    replayer = TickReplayer(vault)
    result = replayer.replay_list(
        Exchange.KIS, "005930", trade_day, ReplaySpec(speed=-1, start_offset=3)
    )

    assert len(result) == 7
    assert result[0].last == 75_003.0


def test_replay_respects_limit(tmp_path: Path) -> None:
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)
    _seed_vault(vault, 10, trade_day)

    replayer = TickReplayer(vault)
    result = replayer.replay_list(
        Exchange.KIS, "005930", trade_day, ReplaySpec(speed=-1, limit=4)
    )

    assert len(result) == 4
    assert result[-1].last == 75_003.0


def test_replay_offset_and_limit_combined(tmp_path: Path) -> None:
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)
    _seed_vault(vault, 10, trade_day)

    replayer = TickReplayer(vault)
    result = replayer.replay_list(
        Exchange.KIS,
        "005930",
        trade_day,
        ReplaySpec(speed=-1, start_offset=2, limit=3),
    )

    assert [t.last for t in result] == [75_002.0, 75_003.0, 75_004.0]


def test_replay_empty_when_no_data(tmp_path: Path) -> None:
    vault = TickVault(root_dir=tmp_path)
    replayer = TickReplayer(vault)

    # 동기 제너레이터 소비
    result = list(replayer.replay(Exchange.KIS, "999999", date(2000, 1, 1)))
    assert result == []


def test_replay_sync_iterator_unlimited_speed(tmp_path: Path) -> None:
    """sync replay()도 speed=-1이면 sleep 0."""
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)
    _seed_vault(vault, 3, trade_day)

    replayer = TickReplayer(vault)
    collected = []
    for tick in replayer.replay(
        Exchange.KIS, "005930", trade_day, ReplaySpec(speed=-1)
    ):
        collected.append(tick)

    assert len(collected) == 3
    assert collected[0].symbol == "005930"


# ======================================================================
# 비동기 재생
# ======================================================================


def test_replay_async_iterator(tmp_path: Path) -> None:
    """async iterator 동작 — speed=-1로 sleep 제거."""
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)
    _seed_vault(vault, 4, trade_day)

    replayer = TickReplayer(vault)

    async def consume() -> list[TickPoint]:
        out: list[TickPoint] = []
        async for tick in replayer.replay_async(
            Exchange.KIS, "005930", trade_day, ReplaySpec(speed=-1)
        ):
            out.append(tick)
        return out

    result = asyncio.run(consume())
    assert len(result) == 4
    assert [t.last for t in result] == [75_000.0, 75_001.0, 75_002.0, 75_003.0]


def test_replay_async_respects_limit(tmp_path: Path) -> None:
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)
    _seed_vault(vault, 8, trade_day)

    replayer = TickReplayer(vault)

    async def consume() -> list[TickPoint]:
        out: list[TickPoint] = []
        async for tick in replayer.replay_async(
            Exchange.KIS, "005930", trade_day, ReplaySpec(speed=-1, limit=2)
        ):
            out.append(tick)
        return out

    result = asyncio.run(consume())
    assert len(result) == 2


# ======================================================================
# preview / 통계
# ======================================================================


def test_preview_reports_counts_and_duration(tmp_path: Path) -> None:
    vault = TickVault(root_dir=tmp_path, flush_interval=100)
    trade_day = date(2026, 4, 11)
    # 0초, 10초, 20초 간격
    vault.append(_make_tick(0, day=trade_day))
    vault.append(_make_tick(10, day=trade_day))
    vault.append(_make_tick(20, day=trade_day))
    vault.flush_all()

    replayer = TickReplayer(vault)
    preview = replayer.preview(Exchange.KIS, "005930", trade_day)

    assert preview["tick_count"] == 3
    assert preview["duration_seconds"] == 20.0
    assert preview["first_timestamp"] is not None
    assert preview["last_timestamp"] is not None


def test_preview_empty_when_missing(tmp_path: Path) -> None:
    vault = TickVault(root_dir=tmp_path)
    replayer = TickReplayer(vault)
    preview = replayer.preview(Exchange.KIS, "999999", date(2000, 1, 1))

    assert preview["tick_count"] == 0
    assert preview["first_timestamp"] is None
    assert preview["duration_seconds"] == 0.0
