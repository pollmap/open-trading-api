"""
Luxon Terminal — TickVault Replay Engine (Sprint 3)

저장된 TickVault 파일을 재생하는 엔진. 동기/비동기 두 인터페이스 제공.

사용 시나리오:
    1. 백테스트 복기 — 특정 날짜의 틱을 그대로 재생하며 전략 신호 재계산
    2. 시각화 디버깅 — LuxonChart에 과거 틱을 1x~무한속도로 쏟아 넣음
    3. E2E 테스트 — 실 API 없이 저장된 샘플로 파이프라인 smoke

속도 옵션 (ReplaySpec.speed):
    > 0 : 실시간 스케일. 1.0=실제, 2.0=2배 가속, 0.5=절반 감속
    <= 0: 무한속도 (sleep 없이 즉시 다음 틱)

재사용 원칙:
    - TickVault.load_day()를 그대로 사용 (로드 로직 중복 금지)
    - schema.ReplaySpec를 SSOT로 사용 (자체 dataclass 새로 만들지 않음)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime
from typing import Any

from kis_backtest.luxon.stream.schema import Exchange, ReplaySpec, TickPoint
from kis_backtest.luxon.stream.tick_vault import TickVault

logger = logging.getLogger(__name__)


def _apply_spec(
    ticks: list[TickPoint], spec: ReplaySpec
) -> list[TickPoint]:
    """ReplaySpec의 start_offset / limit을 적용한 슬라이스.

    timestamp 기준 오름차순 정렬은 TickVault가 append 순서를 보존하므로
    기본적으로 보장되지만, 외부에서 병합된 파일이 들어올 수 있으므로
    안전하게 한 번 더 정렬한다.
    """
    if not ticks:
        return []
    ordered = sorted(ticks, key=lambda t: t.timestamp)
    offset = min(spec.start_offset, len(ordered))
    sliced = ordered[offset:]
    if spec.limit is not None:
        sliced = sliced[: spec.limit]
    return sliced


def _compute_delay(
    prev: TickPoint | None, current: TickPoint, spec: ReplaySpec
) -> float:
    """직전 틱 대비 대기 시간(초). speed 반영.

    무한속도(`spec.is_unlimited_speed`)면 0.
    prev가 None이면 첫 틱이라 대기 없음.
    speed=2면 gap의 절반, speed=0.5면 두 배 대기.
    """
    if spec.is_unlimited_speed or prev is None:
        return 0.0
    gap = (current.timestamp - prev.timestamp).total_seconds()
    if gap <= 0:
        return 0.0
    return gap / spec.speed


class TickReplayer:
    """TickVault 재생 엔진.

    한 인스턴스는 여러 (exchange, symbol, day) 재생에 재사용 가능.
    내부 상태는 없고 단순 파사드.
    """

    def __init__(self, vault: TickVault) -> None:
        self._vault = vault

    @property
    def vault(self) -> TickVault:
        return self._vault

    # ------------------------------------------------------------------
    # 동기 재생
    # ------------------------------------------------------------------

    def replay(
        self,
        exchange: Exchange,
        symbol: str,
        day: date,
        spec: ReplaySpec | None = None,
    ) -> Iterator[TickPoint]:
        """동기 iterator. speed에 따라 time.sleep 호출.

        Example:
            for tick in replayer.replay(Exchange.KIS, "005930", today):
                strategy.on_tick(tick)
        """
        resolved_spec = spec or ReplaySpec()
        raw = self._vault.load_day(exchange, symbol, day)
        ticks = _apply_spec(raw, resolved_spec)
        if not ticks:
            logger.info(
                "TickReplayer: 재생할 틱 없음 (%s/%s/%s)",
                exchange.value,
                symbol,
                day.isoformat(),
            )
            return

        prev: TickPoint | None = None
        for tick in ticks:
            delay = _compute_delay(prev, tick, resolved_spec)
            if delay > 0:
                time.sleep(delay)
            yield tick
            prev = tick

    def replay_list(
        self,
        exchange: Exchange,
        symbol: str,
        day: date,
        spec: ReplaySpec | None = None,
    ) -> list[TickPoint]:
        """sleep 없이 전체 리스트 반환 (테스트/검증용 헬퍼).

        speed 옵션과 상관없이 즉시 반환. 순서 검증에 사용.
        """
        resolved_spec = spec or ReplaySpec(speed=-1)  # 무한속도
        raw = self._vault.load_day(exchange, symbol, day)
        return _apply_spec(raw, resolved_spec)

    # ------------------------------------------------------------------
    # 비동기 재생
    # ------------------------------------------------------------------

    async def replay_async(
        self,
        exchange: Exchange,
        symbol: str,
        day: date,
        spec: ReplaySpec | None = None,
    ) -> AsyncIterator[TickPoint]:
        """async iterator. asyncio.sleep 사용.

        Example:
            async for tick in replayer.replay_async(Exchange.KIS, "005930", today):
                await bus.publish("ticks.kis", tick)
        """
        resolved_spec = spec or ReplaySpec()
        raw = self._vault.load_day(exchange, symbol, day)
        ticks = _apply_spec(raw, resolved_spec)
        if not ticks:
            logger.info(
                "TickReplayer(async): 재생할 틱 없음 (%s/%s/%s)",
                exchange.value,
                symbol,
                day.isoformat(),
            )
            return

        prev: TickPoint | None = None
        for tick in ticks:
            delay = _compute_delay(prev, tick, resolved_spec)
            if delay > 0:
                await asyncio.sleep(delay)
            yield tick
            prev = tick

    # ------------------------------------------------------------------
    # 통계
    # ------------------------------------------------------------------

    def preview(
        self,
        exchange: Exchange,
        symbol: str,
        day: date,
    ) -> dict[str, Any]:
        """재생 전 미리보기 — 몇 틱 / 시간 범위 / 예상 소요 (1x 기준)."""
        ticks = self._vault.load_day(exchange, symbol, day)
        if not ticks:
            return {
                "tick_count": 0,
                "first_timestamp": None,
                "last_timestamp": None,
                "duration_seconds": 0.0,
            }
        ordered = sorted(ticks, key=lambda t: t.timestamp)
        first = ordered[0].timestamp
        last = ordered[-1].timestamp
        return {
            "tick_count": len(ordered),
            "first_timestamp": first,
            "last_timestamp": last,
            "duration_seconds": (last - first).total_seconds(),
        }


__all__ = ["TickReplayer"]
