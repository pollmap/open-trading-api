"""
Luxon Terminal — ConvictionBridge 단위 테스트 (Sprint 4 STEP 3 / 4E)

3중 게이트 어댑터의 순수 단위 테스트. 실 MCP 호출 없음 — Phase1CheckpointResult
를 수동으로 조립하여 각 게이트 경로를 독립적으로 검증한다.

커버 경로:
    1. test_all_three_gates_pass_expansion  — 정상 경로 (EXPANSION → BUY)
    2. test_recovery_regime_buys_with_multiplier — RECOVERY (0.8x)
    3. test_gate1_fail_pipeline_dead        — 전체 사망 → HOLD
    4. test_gate1_partial_success_proceeds  — 부분 성공도 Gate 2로 진행
    5. test_gate2_crisis_regime             → SELL
    6. test_gate2_contraction_regime        → REDUCE
    7. test_gate2_regime_none               — classify 실패 → HOLD
    8. test_gate3_low_conviction            — min 미만 → HOLD
    9. test_proposal_reason_contains_evidence  — 감사 로그 검증
   10. test_invalid_total_capital_raises   — caller 실수는 raise

실데이터 원칙:
    ConvictionSizer는 **실제 인스턴스** 사용. 목업 금지. Phase1CheckpointResult
    는 프레임워크의 진짜 dataclass를 인스턴스화한다 (stub 아님).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from kis_backtest.luxon.integration.conviction_bridge import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_REDUCE,
    ACTION_SELL,
    ConvictionBridge,
    OrderProposal,
)
from kis_backtest.luxon.integration.phase1_pipeline import Phase1CheckpointResult
from kis_backtest.portfolio.conviction_sizer import ConvictionSizer
from kis_backtest.portfolio.macro_regime import Regime, RegimeResult


# ---------------------------------------------------------------------------
# Helpers — Phase1CheckpointResult를 손으로 조립
# ---------------------------------------------------------------------------


def _make_regime_result(
    regime: Regime = Regime.EXPANSION,
    confidence: float = 0.85,
    score: float = 5.5,
) -> RegimeResult:
    """RegimeResult 인스턴스 — allocation은 정상 경로 sample."""
    return RegimeResult(
        regime=regime,
        confidence=confidence,
        score=score,
        positive_signals=7,
        negative_signals=2,
        neutral_signals=1,
        allocation={"equity": 0.70, "cash": 0.30},
    )


def _make_checkpoint(
    regime: Regime | None = Regime.EXPANSION,
    errors: list[str] | None = None,
    fred_series_loaded: int = 10,
    macro_indicator_count: int = 10,
) -> Phase1CheckpointResult:
    """정상 경로 체크포인트. regime=None 이면 classify 실패 시뮬레이션."""
    regime_result = _make_regime_result(regime=regime) if regime else None
    return Phase1CheckpointResult(
        timestamp=datetime.now(),
        fred_series_loaded=fred_series_loaded,
        fred_stale_count=0,
        tick_vault_stats={"total_files": 0, "buffered_keys": 0},
        regime_result=regime_result,
        macro_indicator_count=macro_indicator_count,
        errors=errors or [],
    )


def _default_sizer() -> ConvictionSizer:
    """기본 사이저: max 20%, min 2%, half-kelly."""
    return ConvictionSizer(
        max_position_pct=0.20,
        min_position_pct=0.02,
        kelly_fraction=0.5,
    )


# ---------------------------------------------------------------------------
# Gate 1 통과 + Gate 2 BUY 경로
# ---------------------------------------------------------------------------


def test_all_three_gates_pass_expansion() -> None:
    """정상 경로: EXPANSION regime + 높은 확신도 → BUY."""
    checkpoint = _make_checkpoint(regime=Regime.EXPANSION)
    bridge = ConvictionBridge(_default_sizer())

    proposal = bridge.propose(
        checkpoint=checkpoint,
        symbol="005930",
        base_conviction=8.0,
        total_capital=100_000_000,
    )

    assert isinstance(proposal, OrderProposal)
    assert proposal.action == ACTION_BUY
    assert proposal.symbol == "005930"
    assert proposal.position_pct > 0.0
    # conviction 8.0 → normalized=7/9=0.778 → kelly=0.389 → capped 0.20
    # regime_mult=1.0 → effective=0.20
    assert proposal.position_pct == pytest.approx(0.20, abs=1e-4)
    assert proposal.conviction == pytest.approx(8.0, abs=1e-4)
    assert proposal.passed_gates == ("gate1", "gate2:expansion", "gate3")
    assert proposal.is_actionable is True


def test_recovery_regime_buys_with_lower_multiplier() -> None:
    """Druckenmiller 회복기 매수: RECOVERY도 BUY, 단 0.8x 배수."""
    checkpoint = _make_checkpoint(regime=Regime.RECOVERY)
    bridge = ConvictionBridge(_default_sizer())

    proposal = bridge.propose(
        checkpoint=checkpoint,
        symbol="000660",
        base_conviction=8.0,
        total_capital=100_000_000,
    )

    assert proposal.action == ACTION_BUY
    # EXPANSION에서 0.20 → RECOVERY는 0.20 * 0.8 = 0.16
    assert proposal.position_pct == pytest.approx(0.16, abs=1e-4)
    assert "gate2:recovery" in proposal.passed_gates
    assert "recovery" in proposal.reason.lower()


# ---------------------------------------------------------------------------
# Gate 1 경로
# ---------------------------------------------------------------------------


def test_gate1_fail_pipeline_dead() -> None:
    """파이프라인 전체 사망 (fred=0, regime=None, vault={}) → HOLD."""
    checkpoint = Phase1CheckpointResult(
        timestamp=datetime.now(),
        fred_series_loaded=0,
        fred_stale_count=0,
        tick_vault_stats={},
        regime_result=None,
        macro_indicator_count=0,
        errors=["FRED: ConnectionError", "MacroRegime.fetch: Timeout"],
    )
    bridge = ConvictionBridge(_default_sizer())

    proposal = bridge.propose(
        checkpoint=checkpoint,
        symbol="005930",
        base_conviction=9.0,
        total_capital=100_000_000,
    )

    assert proposal.action == ACTION_HOLD
    assert proposal.position_pct == 0.0
    assert proposal.passed_gates == ()
    assert "gate1" in proposal.reason
    assert proposal.is_actionable is False


def test_gate1_partial_success_proceeds_to_gate2() -> None:
    """errors가 있지만 regime_result는 살아있으면 partial → Gate 2 진행."""
    checkpoint = Phase1CheckpointResult(
        timestamp=datetime.now(),
        fred_series_loaded=0,  # FRED 실패
        fred_stale_count=0,
        tick_vault_stats={},
        regime_result=_make_regime_result(regime=Regime.EXPANSION),  # 생존
        macro_indicator_count=9,
        errors=["FRED: ConnectionError"],
    )
    bridge = ConvictionBridge(_default_sizer())

    proposal = bridge.propose(
        checkpoint=checkpoint,
        symbol="005930",
        base_conviction=8.0,
        total_capital=100_000_000,
    )

    assert proposal.action == ACTION_BUY
    assert proposal.passed_gates[0] == "gate1:partial"
    assert "gate2:expansion" in proposal.passed_gates
    assert "gate3" in proposal.passed_gates


# ---------------------------------------------------------------------------
# Gate 2 경로
# ---------------------------------------------------------------------------


def test_gate2_crisis_regime_triggers_sell() -> None:
    """CRISIS → SELL, position_pct=0 (청산 시그널)."""
    checkpoint = _make_checkpoint(regime=Regime.CRISIS)
    bridge = ConvictionBridge(_default_sizer())

    proposal = bridge.propose(
        checkpoint=checkpoint,
        symbol="005930",
        base_conviction=9.5,  # 확신도 높아도 상관없음
        total_capital=100_000_000,
    )

    assert proposal.action == ACTION_SELL
    assert proposal.position_pct == 0.0
    assert "gate2:crisis" in proposal.passed_gates
    assert "gate3" not in proposal.passed_gates  # Gate 3 스킵
    assert "crisis" in proposal.reason.lower()
    assert proposal.is_actionable is True  # SELL도 즉시 실행


def test_gate2_contraction_regime_triggers_reduce() -> None:
    """CONTRACTION → REDUCE, position_pct=0 (신규 차단, 기존 리듀스)."""
    checkpoint = _make_checkpoint(regime=Regime.CONTRACTION)
    bridge = ConvictionBridge(_default_sizer())

    proposal = bridge.propose(
        checkpoint=checkpoint,
        symbol="005930",
        base_conviction=8.0,
        total_capital=100_000_000,
    )

    assert proposal.action == ACTION_REDUCE
    assert proposal.position_pct == 0.0
    assert "gate2:contraction" in proposal.passed_gates
    assert "gate3" not in proposal.passed_gates
    assert "contraction" in proposal.reason.lower()
    assert proposal.is_actionable is False  # REDUCE는 즉시 실행 아님


def test_gate2_regime_none_holds() -> None:
    """classify_regime() 실패 (regime_result=None) → HOLD."""
    checkpoint = _make_checkpoint(regime=None)
    # 주의: regime=None이면 _make_checkpoint가 regime_result를 None으로 설정.
    # 하지만 fred_series_loaded=10이므로 partial_success=True → Gate 1 통과.
    bridge = ConvictionBridge(_default_sizer())

    proposal = bridge.propose(
        checkpoint=checkpoint,
        symbol="005930",
        base_conviction=8.0,
        total_capital=100_000_000,
    )

    assert proposal.action == ACTION_HOLD
    assert proposal.passed_gates == ("gate1",)  # gate1만 통과
    assert "gate2" in proposal.reason
    assert "regime unknown" in proposal.reason.lower()


# ---------------------------------------------------------------------------
# Gate 3 경로
# ---------------------------------------------------------------------------


def test_gate3_low_conviction_holds() -> None:
    """확신도가 낮아 kelly_raw < min_position_pct → HOLD."""
    checkpoint = _make_checkpoint(regime=Regime.EXPANSION)
    # min_position_pct를 높여서 낮은 확신도가 통과 못 하게
    sizer = ConvictionSizer(
        max_position_pct=0.20,
        min_position_pct=0.10,  # 10% 미만은 전부 0
        kelly_fraction=0.5,
    )
    bridge = ConvictionBridge(sizer)

    # conviction 2.0 → normalized = 1/9 ≈ 0.111 → kelly = 0.0555 < 0.10
    proposal = bridge.propose(
        checkpoint=checkpoint,
        symbol="005930",
        base_conviction=2.0,
        total_capital=100_000_000,
    )

    assert proposal.action == ACTION_HOLD
    assert proposal.position_pct == 0.0
    assert "gate1" in proposal.passed_gates
    assert "gate2:expansion" in proposal.passed_gates
    assert "gate3" not in proposal.passed_gates  # Gate 3 탈락
    assert "gate3" in proposal.reason
    assert "conviction too low" in proposal.reason.lower()


# ---------------------------------------------------------------------------
# 감사 로그 / caller 실수
# ---------------------------------------------------------------------------


def test_proposal_reason_contains_evidence() -> None:
    """BUY 경로 reason에는 regime, conviction, kelly, weight 전부 포함."""
    checkpoint = _make_checkpoint(regime=Regime.EXPANSION)
    bridge = ConvictionBridge(_default_sizer())

    proposal = bridge.propose(
        checkpoint=checkpoint,
        symbol="005930",
        base_conviction=7.5,
        total_capital=100_000_000,
    )

    assert proposal.action == ACTION_BUY
    reason_lower = proposal.reason.lower()
    assert "expansion" in reason_lower
    assert "conviction" in reason_lower
    assert "kelly" in reason_lower
    assert "weight" in reason_lower
    assert "effective" in reason_lower
    # passed_gates 길이는 BUY 경로에서 3
    assert len(proposal.passed_gates) == 3


def test_invalid_total_capital_raises() -> None:
    """total_capital <= 0은 caller 실수 — HOLD가 아닌 raise."""
    checkpoint = _make_checkpoint(regime=Regime.EXPANSION)
    bridge = ConvictionBridge(_default_sizer())

    with pytest.raises(ValueError, match="total_capital must be positive"):
        bridge.propose(
            checkpoint=checkpoint,
            symbol="005930",
            base_conviction=8.0,
            total_capital=0,
        )

    with pytest.raises(ValueError):
        bridge.propose(
            checkpoint=checkpoint,
            symbol="005930",
            base_conviction=8.0,
            total_capital=-1_000_000,
        )
