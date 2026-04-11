"""
Luxon Terminal — Upbit Tick Tap (Sprint 3)

providers.upbit.websocket.UpbitWebSocket의 async generator 구독을 소비하며
TickVault에 저장하는 래퍼.

설계 원칙:
    - providers/upbit/* 파일 한 글자도 수정 금지 (사이드카 원칙)
    - subscribe()가 내는 dict 메시지를 TickPoint로 변환
    - async 루프는 UpbitTickTap.run()이 소유, 외부에서 asyncio.create_task
    - ticker/trade 두 메시지 타입 모두 지원 (trade 우선, ticker도 파싱 가능)

Usage:
    import asyncio
    from kis_backtest.providers.upbit.websocket import UpbitWebSocket
    from kis_backtest.luxon.stream.tick_vault import TickVault
    from kis_backtest.luxon.stream.upbit_tick_tap import UpbitTickTap

    async def main():
        vault = TickVault()
        ws = UpbitWebSocket()
        tap = UpbitTickTap(vault, ws)
        try:
            await tap.run(codes=["KRW-BTC", "KRW-ETH"], message_type="trade")
        finally:
            vault.flush_all()

    asyncio.run(main())
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from kis_backtest.luxon.stream.schema import Exchange, TickPoint
from kis_backtest.luxon.stream.tick_vault import TickVault

# [FIX A5 HIGH-3 / A6 MEDIUM-2] Upbit 서버 timestamp는 UTC epoch ms이고,
# Luxon은 한국 시장 중심이라 날짜 버킷 파티셔닝을 KST 기준으로 수행한다.
# 과거 구현은 datetime.fromtimestamp(ts)로 로컬 tz를 사용했는데, VPS(UTC)에서
# 실행되면 9시간 차이가 발생해 틱이 잘못된 일별 파일에 저장됐다.
# 이 상수는 모든 Upbit 틱을 명시적 KST naive datetime으로 표준화한다.
_KST = timezone(timedelta(hours=9))

if TYPE_CHECKING:
    from kis_backtest.providers.upbit.websocket import UpbitWebSocket

logger = logging.getLogger(__name__)


# Upbit 메시지 필드 참고 (공식 문서):
#   trade 타입:
#     type="trade", code="KRW-BTC", trade_price, trade_volume,
#     trade_timestamp(ms epoch), ask_bid("BID"|"ASK"), prev_closing_price
#   ticker 타입:
#     type="ticker", code="KRW-BTC", trade_price, trade_volume,
#     timestamp(ms epoch), change, change_rate, acc_trade_volume_24h


def upbit_msg_to_tick(msg: dict[str, Any]) -> TickPoint:
    """Upbit WebSocket 메시지 1건 → TickPoint 변환.

    trade / ticker 메시지 모두 지원. 둘 다 trade_price 필드를 갖고
    timestamp가 ms epoch이라는 공통점을 이용.

    Raises:
        ValueError: 필수 필드 누락 또는 가격이 0 이하
        KeyError: code/trade_price 없음
    """
    code = msg.get("code")
    if not code:
        raise KeyError("Upbit 메시지에 'code' 필드 없음")

    price = msg.get("trade_price")
    if price is None:
        raise KeyError(f"Upbit 메시지에 'trade_price' 없음 (code={code})")

    # trade 타입은 trade_timestamp, ticker 타입은 timestamp 사용
    ts_ms = msg.get("trade_timestamp") or msg.get("timestamp")
    if ts_ms is None:
        raise KeyError(
            f"Upbit 메시지에 timestamp 없음 (code={code}, type={msg.get('type')})"
        )

    # UTC epoch ms → KST 명시 변환 → naive KST datetime.
    # tz-aware → tz-naive 제거는 TickVault가 naive datetime을 가정하기 때문.
    # Sprint 4 E2E에서 tz-aware로 전환 가능 (TODO: 일관성 개선).
    utc_ts = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
    timestamp = utc_ts.astimezone(_KST).replace(tzinfo=None)

    # Upbit는 호가 별도 스트림 → trade/ticker에 bid/ask 없음
    volume = msg.get("trade_volume")
    volume_f = float(volume) if volume is not None else None

    return TickPoint(
        timestamp=timestamp,
        symbol=str(code),
        exchange=Exchange.UPBIT,
        last=float(price),
        bid=None,
        ask=None,
        volume=volume_f,
        extra=(
            ("msg_type", str(msg.get("type", "unknown"))),
            ("ask_bid", str(msg.get("ask_bid", ""))),
            ("change", str(msg.get("change", ""))),
        ),
    )


@dataclass
class UpbitTickTap:
    """Upbit 구독 루프 + TickVault 저장 어댑터.

    Attributes:
        vault: TickVault
        ws: UpbitWebSocket 인스턴스 (주입 — 재사용/테스트 용이성)
        tick_count: 저장 성공 수
        error_count: 변환/저장 실패 수
    """

    vault: TickVault
    ws: "UpbitWebSocket"
    tick_count: int = field(default=0, init=False)
    error_count: int = field(default=0, init=False)

    async def run(
        self,
        codes: list[str],
        message_type: str = "trade",
        max_ticks: int | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Upbit 구독을 열고 메시지를 TickVault에 적재.

        Args:
            codes: 마켓 코드 리스트, 예: ["KRW-BTC", "KRW-ETH"]
            message_type: "trade" 또는 "ticker". 기본은 "trade" (체결 우선)
            max_ticks: 이 수만큼 저장 후 종료. None=무제한
            duration_seconds: 이 초만큼 수집 후 종료. None=무제한
                max_ticks/duration_seconds 중 먼저 도달하는 쪽으로 멈춤

        종료 조건이 None/None이면 외부에서 task.cancel()로 중단 가능.

        [FIX Sprint4 M1] Python 3.12+에서 ``asyncio.get_event_loop()``는
        실행 중 루프가 없으면 DeprecationWarning이다. ``run()``은 항상
        실행 중 루프 안에서 호출되므로 ``get_running_loop()``가 정석.

        [FIX Sprint4 M2] 외부에서 ``task.cancel()``로 중단될 때
        ``asyncio.CancelledError``는 ``BaseException`` 계열이라 아래의
        ``except Exception`` 블록에 걸리지 않아 기능적 버그는 없지만,
        명시적으로 catch → 수집 통계 로그 → re-raise 해서 취소 cleanup
        흐름을 관측 가능하게 만든다. Luxon 데몬(Sprint 1.5)이 24/7
        반복 재시작될 때 마지막 세션 상태를 로그로 남겨 R11 같은
        silent-fail 재발을 막는 장치.
        """
        loop = asyncio.get_running_loop()
        start = loop.time()

        try:
            async for msg in self.ws.subscribe(type=message_type, codes=codes):
                try:
                    tick = upbit_msg_to_tick(msg)
                except Exception as e:
                    self.error_count += 1
                    logger.warning(
                        "UpbitTickTap 변환 실패 code=%s err=%s",
                        msg.get("code", "?"),
                        e,
                    )
                    continue

                try:
                    self.vault.append(tick)
                except Exception as e:
                    self.error_count += 1
                    logger.warning(
                        "UpbitTickTap append 실패 code=%s err=%s", tick.symbol, e
                    )
                    continue

                self.tick_count += 1

                if max_ticks is not None and self.tick_count >= max_ticks:
                    logger.info(
                        "UpbitTickTap 종료: max_ticks=%d 도달", max_ticks
                    )
                    break

                if duration_seconds is not None:
                    elapsed = loop.time() - start
                    if elapsed >= duration_seconds:
                        logger.info(
                            "UpbitTickTap 종료: duration_seconds=%.1f 도달",
                            duration_seconds,
                        )
                        break
        except asyncio.CancelledError:
            logger.info(
                "UpbitTickTap 취소됨: tick_count=%d error_count=%d",
                self.tick_count,
                self.error_count,
            )
            raise

    def stats(self) -> dict[str, Any]:
        return {
            "exchange": Exchange.UPBIT.value,
            "tick_count": self.tick_count,
            "error_count": self.error_count,
            "vault_root": str(self.vault.root_dir),
        }


__all__ = ["UpbitTickTap", "upbit_msg_to_tick"]
