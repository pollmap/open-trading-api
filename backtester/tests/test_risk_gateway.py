"""RiskGateway + KillSwitch 테스트

시나리오:
    1. 모든 체크 통과 (paper 모드)
    2. 킬 스위치 활성 → 차단
    3. 파이프라인 리스크 FAIL → 차단
    4. DD 상태 HALT → 차단
    5. 장외 시간 → 차단
    6. 총 매수금액 > 가용현금 → 차단
    7. 단일 주문 > 30% 상한 → 차단
    8. Rate limit 초과 → 차단
    9. KillSwitch 활성화/해제 라이프사이클
    10. is_market_open 시간 체크
"""

import pytest
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

from kis_backtest.core.pipeline import PipelineResult
from kis_backtest.execution.kill_switch import KillSwitch, KillSwitchActiveError
from kis_backtest.execution.risk_gateway import RiskGateway, GatewayDecision
from kis_backtest.execution.models import (
    PlannedTrade,
    TradeReason,
    TransactionCostEstimate,
)
from kis_backtest.models import AccountBalance, OrderSide
from kis_backtest.utils.korean_market import is_market_open, KST


# ─── 헬퍼 ────────────────────────────────────

def _make_pipeline_result(
    risk_passed: bool = True,
    dd_state: str = "NORMAL",
) -> PipelineResult:
    return PipelineResult(
        order=None,
        risk_passed=risk_passed,
        risk_details=["ALL PASS"] if risk_passed else ["Sharpe 0.3 < 0.5"],
        vol_adjustments={},
        turb_index=1.5,
        dd_state=dd_state,
        estimated_annual_cost=0.0276,
        kelly_allocation=0.5,
    )


def _make_balance(
    available_cash: float = 10_000_000,
    total_equity: float = 10_000_000,
) -> AccountBalance:
    return AccountBalance(
        total_cash=available_cash,
        available_cash=available_cash,
        total_equity=total_equity,
        total_pnl=0,
        total_pnl_percent=0,
        currency="KRW",
    )


def _make_trade(
    symbol: str = "005930",
    name: str = "삼성전자",
    side: OrderSide = OrderSide.BUY,
    quantity: int = 10,
    price: float = 70000,
) -> PlannedTrade:
    return PlannedTrade(
        symbol=symbol,
        name=name,
        side=side,
        quantity=quantity,
        estimated_price=price,
        estimated_cost=TransactionCostEstimate(100, 0, 50),
        reason=TradeReason.NEW_ENTRY,
    )


def _market_hours_kst() -> datetime:
    """정규 거래시간 (화요일 10:00 KST)"""
    # 2026-04-07 화요일
    return datetime(2026, 4, 7, 10, 0, tzinfo=KST)


def _after_hours_kst() -> datetime:
    """장 마감 후 (화요일 16:00 KST)"""
    return datetime(2026, 4, 7, 16, 0, tzinfo=KST)


# ─── KillSwitch 테스트 ───────────────────────

class TestKillSwitch:

    def test_lifecycle(self, tmp_path):
        lock_file = tmp_path / "test_kill.lock"
        ks = KillSwitch(lock_path=lock_file)

        assert not ks.is_active
        assert ks.reason == ""

        ks.activate("테스트 긴급 정지")
        assert ks.is_active
        assert "테스트 긴급 정지" in ks.reason

        ks.deactivate()
        assert not ks.is_active

    def test_check_or_raise(self, tmp_path):
        lock_file = tmp_path / "test_kill.lock"
        ks = KillSwitch(lock_path=lock_file)

        # 비활성 시 통과
        ks.check_or_raise()

        # 활성 시 예외
        ks.activate("emergency")
        with pytest.raises(KillSwitchActiveError):
            ks.check_or_raise()

    def test_deactivate_when_not_active(self, tmp_path):
        lock_file = tmp_path / "test_kill.lock"
        ks = KillSwitch(lock_path=lock_file)
        # 이미 비활성 상태에서 해제해도 오류 없음
        ks.deactivate()
        assert not ks.is_active


# ─── RiskGateway 테스트 ──────────────────────

class TestRiskGatewayAllPass:
    """시나리오 1: 모든 체크 통과"""

    def test_all_checks_pass_paper_mode(self, tmp_path):
        ks = KillSwitch(lock_path=tmp_path / "ks.lock")
        gateway = RiskGateway(
            mode="paper",
            kill_switch=ks,
            require_market_hours=False,  # 시간 무관 테스트
        )

        trades = [_make_trade(quantity=10, price=70000)]
        balance = _make_balance(available_cash=10_000_000)
        result = _make_pipeline_result(risk_passed=True)

        decision = gateway.check(trades, balance, result)

        assert decision.approved
        assert len(decision.blocked_trades) == 0
        assert any("✓" in c for c in decision.checks)


class TestKillSwitchBlock:
    """시나리오 2: 킬 스위치 활성 → 차단"""

    def test_kill_switch_blocks(self, tmp_path):
        lock_file = tmp_path / "ks.lock"
        ks = KillSwitch(lock_path=lock_file)
        ks.activate("테스트 정지")

        gateway = RiskGateway(mode="paper", kill_switch=ks, require_market_hours=False)

        decision = gateway.check(
            [_make_trade()],
            _make_balance(),
            _make_pipeline_result(),
        )

        assert not decision.approved
        assert any("킬 스위치" in c for c in decision.checks)


class TestPipelineRiskFail:
    """시나리오 3: 파이프라인 리스크 FAIL"""

    def test_pipeline_fail_blocks(self, tmp_path):
        ks = KillSwitch(lock_path=tmp_path / "ks.lock")
        gateway = RiskGateway(mode="paper", kill_switch=ks, require_market_hours=False)

        decision = gateway.check(
            [_make_trade()],
            _make_balance(),
            _make_pipeline_result(risk_passed=False),
        )

        assert not decision.approved
        assert any("파이프라인 리스크 FAIL" in c for c in decision.checks)


class TestDDHalt:
    """시나리오 4: DD 상태 HALT"""

    def test_dd_halt_blocks(self, tmp_path):
        ks = KillSwitch(lock_path=tmp_path / "ks.lock")
        gateway = RiskGateway(mode="paper", kill_switch=ks, require_market_hours=False)

        decision = gateway.check(
            [_make_trade()],
            _make_balance(),
            _make_pipeline_result(dd_state="HALT"),
        )

        assert not decision.approved
        assert any("HALT" in c for c in decision.checks)


class TestMarketHours:
    """시나리오 5: 장외 시간"""

    def test_after_hours_blocks(self, tmp_path):
        ks = KillSwitch(lock_path=tmp_path / "ks.lock")
        gateway = RiskGateway(mode="paper", kill_switch=ks, require_market_hours=True)

        decision = gateway.check(
            [_make_trade()],
            _make_balance(),
            _make_pipeline_result(),
            now=_after_hours_kst(),
        )

        assert not decision.approved
        assert any("장외" in c for c in decision.checks)

    def test_market_hours_pass(self, tmp_path):
        ks = KillSwitch(lock_path=tmp_path / "ks.lock")
        gateway = RiskGateway(mode="paper", kill_switch=ks, require_market_hours=True)

        decision = gateway.check(
            [_make_trade()],
            _make_balance(),
            _make_pipeline_result(),
            now=_market_hours_kst(),
        )

        assert decision.approved


class TestCashExceeded:
    """시나리오 6: 총 매수금액 > 가용현금"""

    def test_cash_exceeded_blocks(self, tmp_path):
        ks = KillSwitch(lock_path=tmp_path / "ks.lock")
        gateway = RiskGateway(mode="paper", kill_switch=ks, require_market_hours=False)

        # 매수금액 700,000원 > 가용현금 500,000원
        trades = [_make_trade(quantity=10, price=70000)]  # 700,000원
        balance = _make_balance(available_cash=500_000)

        decision = gateway.check(trades, balance, _make_pipeline_result())

        assert not decision.approved
        assert any("매수 총액" in c and "가용현금" in c for c in decision.checks)


class TestSingleOrderLimit:
    """시나리오 7: 단일 주문 > 30% 상한"""

    def test_single_order_exceeds_limit(self, tmp_path):
        ks = KillSwitch(lock_path=tmp_path / "ks.lock")
        gateway = RiskGateway(mode="paper", kill_switch=ks, require_market_hours=False)

        # 단일 주문 3,500,000원 / 가용 10,000,000원 = 35% > 30%
        trades = [_make_trade(quantity=50, price=70000)]  # 3,500,000원
        balance = _make_balance(available_cash=10_000_000)

        decision = gateway.check(trades, balance, _make_pipeline_result())

        assert not decision.approved
        assert len(decision.blocked_trades) > 0


class TestRateLimit:
    """시나리오 8: Rate limit"""

    def test_rate_limit_blocks_excess(self, tmp_path):
        ks = KillSwitch(lock_path=tmp_path / "ks.lock")
        gateway = RiskGateway(mode="paper", kill_switch=ks, require_market_hours=False)

        small_trade = _make_trade(quantity=1, price=100000)
        trades = [small_trade] * 15  # 15건 → 분당 10건 초과

        balance = _make_balance(available_cash=100_000_000)
        decision = gateway.check(trades, balance, _make_pipeline_result())

        assert not decision.approved
        assert any("Rate limit" in c for c in decision.checks)


# ─── is_market_open 테스트 ───────────────────

class TestIsMarketOpen:

    def test_weekday_market_hours(self):
        # 화요일 10:00 KST
        dt = datetime(2026, 4, 7, 10, 0, tzinfo=KST)
        assert is_market_open(dt) is True

    def test_weekday_before_open(self):
        dt = datetime(2026, 4, 7, 8, 30, tzinfo=KST)
        assert is_market_open(dt) is False

    def test_weekday_after_close(self):
        dt = datetime(2026, 4, 7, 16, 0, tzinfo=KST)
        assert is_market_open(dt) is False

    def test_weekend(self):
        # 2026-04-11 토요일
        dt = datetime(2026, 4, 11, 10, 0, tzinfo=KST)
        assert is_market_open(dt) is False

    def test_market_close_boundary(self):
        # 15:30 정각 = 아직 열려있음 (<=)
        dt = datetime(2026, 4, 7, 15, 30, tzinfo=KST)
        assert is_market_open(dt) is True

    def test_market_open_boundary(self):
        # 09:00 정각 = 열려있음 (>=)
        dt = datetime(2026, 4, 7, 9, 0, tzinfo=KST)
        assert is_market_open(dt) is True


class TestGatewayDecisionSummary:
    """GatewayDecision.summary() 포맷 검증"""

    def test_approved_summary(self):
        decision = GatewayDecision(
            approved=True,
            checks=["✓ [1] 킬 스위치 비활성", "✓ [2] 파이프라인 리스크 PASS"],
            blocked_trades=[],
        )
        summary = decision.summary()
        assert "APPROVED" in summary
        assert "킬 스위치" in summary

    def test_blocked_summary(self):
        decision = GatewayDecision(
            approved=False,
            checks=["✗ [1] 킬 스위치 활성"],
            blocked_trades=["삼성전자: 35% > 30% 상한"],
        )
        summary = decision.summary()
        assert "BLOCKED" in summary
        assert "삼성전자" in summary
