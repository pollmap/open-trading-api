"""
Luxon Terminal — KIS Tick Tap (Sprint 3)

providers.kis.websocket.KISWebSocket의 실시간 체결가 콜백을
TickVault에 저장하는 **얇은 래퍼**.

설계 원칙:
    - providers/kis/* 파일 한 글자도 수정 금지 (사이드카 원칙)
    - KIS subscribe_price(symbols, callback)에 주입할 callback만 제공
    - callback이 호출되면 RealtimePrice → TickPoint 변환 후 vault.append()
    - 에러 발생 시 silent-fail 금지 — log.warning + 카운터 증가

Usage:
    from kis_backtest.providers.kis.auth import KISAuth
    from kis_backtest.providers.kis.websocket import KISWebSocket
    from kis_backtest.luxon.stream.tick_vault import TickVault
    from kis_backtest.luxon.stream.kis_tick_tap import KISTickTap

    vault = TickVault()
    tap = KISTickTap(vault)
    auth = KISAuth.from_env("paper")
    ws = KISWebSocket.from_auth(auth)

    ws.subscribe_price(["005930", "000660"], tap.on_realtime_price)
    ws.start()  # 블로킹

    # 종료 시
    vault.flush_all()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any

from kis_backtest.luxon.stream.schema import Exchange, TickPoint
from kis_backtest.luxon.stream.tick_vault import TickVault

if TYPE_CHECKING:
    # providers/kis에 의존하는 타입 힌트는 lazy import (순환 참조 방지)
    from kis_backtest.providers.kis.websocket import RealtimePrice

logger = logging.getLogger(__name__)


def kis_realtime_price_to_tick(
    symbol: str,
    rt_price: "RealtimePrice",
    trade_date: date | None = None,
) -> TickPoint:
    """KIS RealtimePrice → TickPoint 변환.

    KIS는 시분초를 HHMMSS 문자열로 넘기므로 trade_date와 조합해서
    aware 없는 naive datetime 생성 (KST 기준, Sprint 3에서는 tz 단순화).

    Args:
        symbol: 종목 코드 (콜백 첫 인자)
        rt_price: KIS RealtimePrice dataclass
        trade_date: 체결일. None이면 date.today() (KST 서버 시각 가정)

    Raises:
        ValueError: HHMMSS 파싱 실패 또는 가격이 0 이하
    """
    if trade_date is None:
        trade_date = date.today()

    hhmmss = rt_price.time or "000000"
    # KIS는 "HHMMSS" 6자리 또는 "HH:MM:SS" 8자리 둘 다 받을 수 있음
    if ":" in hhmmss:
        parts = hhmmss.split(":")
        if len(parts) != 3:
            raise ValueError(f"잘못된 KIS time 포맷: {hhmmss!r}")
        hh, mm, ss = (int(p) for p in parts)
    else:
        if len(hhmmss) < 6:
            hhmmss = hhmmss.rjust(6, "0")
        hh = int(hhmmss[0:2])
        mm = int(hhmmss[2:4])
        ss = int(hhmmss[4:6])

    timestamp = datetime.combine(trade_date, time(hh, mm, ss))

    # bid/ask 0은 "데이터 없음"으로 처리 (주문 공백)
    bid = float(rt_price.bid_price) if rt_price.bid_price > 0 else None
    ask = float(rt_price.ask_price) if rt_price.ask_price > 0 else None

    return TickPoint(
        timestamp=timestamp,
        symbol=symbol,
        exchange=Exchange.KIS,
        last=float(rt_price.price),
        bid=bid,
        ask=ask,
        volume=float(rt_price.volume) if rt_price.volume >= 0 else None,
        extra=(
            ("change_sign", rt_price.change_sign),
            ("change_rate", rt_price.change_rate),
            ("total_volume", rt_price.total_volume),
        ),
    )


@dataclass
class KISTickTap:
    """KIS WebSocket 콜백 → TickVault 저장 어댑터.

    상태는 최소화 — vault 주입 + 카운터만. 재연결/에러 로직은
    KISWebSocket 자체가 담당하므로 여기서는 변환+append만.

    Attributes:
        vault: 주입된 TickVault (쓰기 가능)
        trade_date: 체결일 override (테스트/재생 시에만). None이면 매 콜백마다
            date.today() 조회 → 자정 경계에서도 자동 전환.
        tick_count: 성공적으로 저장한 틱 수 (관측)
        error_count: 변환/저장 실패 수 (관측)
    """

    vault: TickVault
    trade_date: date | None = None
    tick_count: int = field(default=0, init=False)
    error_count: int = field(default=0, init=False)

    def on_realtime_price(
        self, symbol: str, rt_price: "RealtimePrice"
    ) -> None:
        """KISWebSocket.subscribe_price에 주입할 콜백.

        시그니처는 providers.kis.websocket의 Callable[[str, RealtimePrice], None]과
        정확히 일치해야 함. 내부에서 silent-fail 금지 (R11 재발 방지).
        """
        try:
            tick = kis_realtime_price_to_tick(
                symbol=symbol,
                rt_price=rt_price,
                trade_date=self.trade_date,
            )
        except Exception as e:
            self.error_count += 1
            logger.warning(
                "KISTickTap 변환 실패 symbol=%s time=%s err=%s",
                symbol,
                getattr(rt_price, "time", "?"),
                e,
            )
            return

        try:
            self.vault.append(tick)
        except Exception as e:
            self.error_count += 1
            logger.warning(
                "KISTickTap append 실패 symbol=%s err=%s", symbol, e
            )
            return

        self.tick_count += 1

    def stats(self) -> dict[str, Any]:
        """운영 대시보드용 요약."""
        return {
            "exchange": Exchange.KIS.value,
            "tick_count": self.tick_count,
            "error_count": self.error_count,
            "vault_root": str(self.vault.root_dir),
        }


__all__ = ["KISTickTap", "kis_realtime_price_to_tick"]
