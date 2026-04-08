"""주문 전 리스크 게이트웨이

LiveOrderExecutor가 주문을 실행하기 전 7개 체크를 수행.
prod 모드에서는 수동 승인 필수.

체크리스트:
    1. pipeline_result.risk_passed == True
    2. 총 주문금액 ≤ available_cash (마진 사용 안 함)
    3. 단일 주문 ≤ 30% of available_cash
    4. Rate limit: 분당 10건, 초당 2건
    5. 시장 시간 확인 (KST 09:00-15:30)
    6. DD 상태 ≠ HALT
    7. 킬 스위치 비활성
"""

from __future__ import annotations

import logging
import time as time_module
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Literal, Optional

from kis_backtest.core.pipeline import PipelineResult
from kis_backtest.execution.kill_switch import KillSwitch
from kis_backtest.execution.models import PlannedTrade
from kis_backtest.models import AccountBalance, OrderSide
from kis_backtest.utils.korean_market import is_market_open

logger = logging.getLogger(__name__)

# Rate limit 설정
MAX_ORDERS_PER_MINUTE = 10
MAX_ORDERS_PER_SECOND = 2
MAX_SINGLE_ORDER_RATIO = 0.30  # 단일 주문 ≤ 가용현금 30%


@dataclass(frozen=True)
class GatewayDecision:
    """게이트웨이 판정 결과"""
    approved: bool
    checks: List[str]           # 통과/실패 메시지
    blocked_trades: List[str]   # 차단된 거래 사유

    def summary(self) -> str:
        status = "APPROVED" if self.approved else "BLOCKED"
        lines = [f"=== Risk Gateway: {status} ==="]
        for check in self.checks:
            lines.append(f"  {check}")
        if self.blocked_trades:
            lines.append("--- 차단된 거래 ---")
            for msg in self.blocked_trades:
                lines.append(f"  ✗ {msg}")
        return "\n".join(lines)


class RiskGateway:
    """주문 전 리스크 게이트

    Usage:
        gateway = RiskGateway(mode="paper")
        decision = gateway.check(planned_trades, balance, pipeline_result)
        if decision.approved:
            executor.execute(order)
    """

    def __init__(
        self,
        mode: Literal["paper", "prod"] = "paper",
        kill_switch: Optional[KillSwitch] = None,
        max_single_order_ratio: float = MAX_SINGLE_ORDER_RATIO,
        require_market_hours: bool = True,
    ):
        self._mode = mode
        self._kill_switch = kill_switch or KillSwitch()
        self._max_single_ratio = max_single_order_ratio
        self._require_market_hours = require_market_hours
        self._order_timestamps: deque[float] = deque(maxlen=MAX_ORDERS_PER_MINUTE)

    def check(
        self,
        planned_trades: List[PlannedTrade],
        balance: AccountBalance,
        pipeline_result: PipelineResult,
        now: Optional[datetime] = None,
    ) -> GatewayDecision:
        """7개 리스크 체크 수행

        Returns:
            GatewayDecision: approved=True면 주문 진행 가능
        """
        checks: List[str] = []
        blocked: List[str] = []
        all_passed = True

        # 1. 킬 스위치
        if self._kill_switch.is_active:
            checks.append(f"✗ [1] 킬 스위치 활성: {self._kill_switch.reason}")
            all_passed = False
        else:
            checks.append("✓ [1] 킬 스위치 비활성")

        # 2. 파이프라인 리스크 게이트
        if not pipeline_result.risk_passed:
            details = ", ".join(pipeline_result.risk_details)
            checks.append(f"✗ [2] 파이프라인 리스크 FAIL: {details}")
            all_passed = False
        else:
            checks.append("✓ [2] 파이프라인 리스크 PASS")

        # 3. DD 상태
        if pipeline_result.dd_state == "HALT":
            checks.append("✗ [3] DD 상태 HALT — 전량 청산 필요")
            all_passed = False
        elif pipeline_result.dd_state == "REDUCE":
            checks.append("⚠ [3] DD 상태 REDUCE — 50% 축소 적용")
        else:
            dd_display = pipeline_result.dd_state or "NORMAL"
            checks.append(f"✓ [3] DD 상태: {dd_display}")

        # 4. 시장 시간
        if self._require_market_hours and not is_market_open(now):
            checks.append("✗ [4] 장외 시간 — 주문 불가")
            all_passed = False
        else:
            checks.append("✓ [4] 시장 시간 확인")

        # 5. 총 주문금액 vs 가용현금
        total_buy = sum(
            t.estimated_amount for t in planned_trades
            if t.side == OrderSide.BUY
        )
        if total_buy > balance.available_cash:
            checks.append(
                f"✗ [5] 매수 총액 {total_buy:,.0f}원 > "
                f"가용현금 {balance.available_cash:,.0f}원"
            )
            all_passed = False
        else:
            checks.append(
                f"✓ [5] 매수 총액 {total_buy:,.0f}원 ≤ "
                f"가용현금 {balance.available_cash:,.0f}원"
            )

        # 6. 단일 주문 상한
        for trade in planned_trades:
            if trade.side == OrderSide.BUY:
                ratio = trade.estimated_amount / balance.available_cash if balance.available_cash > 0 else 1.0
                if ratio > self._max_single_ratio:
                    blocked.append(
                        f"{trade.name}: {ratio*100:.0f}% > {self._max_single_ratio*100:.0f}% 상한"
                    )
        if blocked:
            checks.append(f"✗ [6] 단일 주문 상한 초과: {len(blocked)}건")
            all_passed = False
        else:
            checks.append("✓ [6] 단일 주문 상한 OK")

        # 7. Rate limit
        rate_ok = self._check_rate_limit(len(planned_trades))
        if not rate_ok:
            checks.append(f"✗ [7] Rate limit 초과 (분당 {MAX_ORDERS_PER_MINUTE}건)")
            all_passed = False
        else:
            checks.append("✓ [7] Rate limit OK")

        # prod 모드 수동 승인
        if all_passed and self._mode == "prod":
            approved = self._request_manual_approval(planned_trades)
            if not approved:
                checks.append("✗ [승인] 사용자 거부")
                all_passed = False
            else:
                checks.append("✓ [승인] 사용자 승인 완료")

        return GatewayDecision(
            approved=all_passed,
            checks=checks,
            blocked_trades=blocked,
        )

    def _check_rate_limit(self, n_orders: int) -> bool:
        """슬라이딩 윈도우 rate limit 체크"""
        now = time_module.time()

        # 1분 이내 주문 수
        while self._order_timestamps and now - self._order_timestamps[0] > 60:
            self._order_timestamps.popleft()

        if len(self._order_timestamps) + n_orders > MAX_ORDERS_PER_MINUTE:
            return False

        # 통과 시 타임스탬프 기록
        for _ in range(n_orders):
            self._order_timestamps.append(now)

        return True

    def _request_manual_approval(
        self,
        trades: List[PlannedTrade],
    ) -> bool:
        """prod 모드 수동 승인 요청

        콘솔에 거래 요약을 출력하고 Y/n 입력을 기다림.
        """
        print("\n" + "=" * 60)
        print("  ⚠️  실전 주문 승인 요청")
        print("=" * 60)
        for trade in trades:
            print(f"  {trade.summary_line()}")
        total = sum(t.estimated_amount for t in trades)
        print(f"\n  총 거래금액: {total:,.0f}원")
        print("=" * 60)

        try:
            response = input("  실행하시겠습니까? (Y/n): ").strip().lower()
            return response in ("y", "yes", "")
        except (EOFError, KeyboardInterrupt):
            logger.warning("승인 입력 중단됨")
            return False
