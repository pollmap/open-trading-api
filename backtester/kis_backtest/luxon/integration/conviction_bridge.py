"""
Luxon Terminal — ConvictionBridge (Sprint 4 STEP 3 / 4E)

3중 게이트 어댑터: `Phase1CheckpointResult` → macro regime → `ConvictionSizer`
→ dry-run `OrderProposal`. Phase 2 GothamGraph 진입 전 Phase 1 마지막 재료.

설계 원칙:
    - **신규 계산 로직 0줄** — ConvictionSizer 내부 수정 금지, 래핑만.
    - **게이트 투명성** — 모든 탈락은 `reason` + `passed_gates`에 기록(감사).
    - **4-state regime 전부 매핑** — RECOVERY를 EXPANSION에 뭉개지 않음
      (Druckenmiller 회복기 매수 로직 보존).
    - **실 주문 금지** — dry-run `OrderProposal`만 반환. `execution/` 진입 금지.

Gate 정책:
    Gate 1 (파이프라인 건강도):
        checkpoint.success or checkpoint.partial_success 이어야 통과.
        양쪽 다 False = 데이터가 하나도 없음 → HOLD.

    Gate 2 (매크로 레짐):
        regime_result가 None이면 판단 불가 → HOLD.
        EXPANSION  → BUY, regime_multiplier = 1.0
        RECOVERY   → BUY, regime_multiplier = 0.8
        CONTRACTION→ REDUCE, position_pct = 0 (신규 매수 차단)
        CRISIS     → SELL, position_pct = 0 (즉시 청산 시그널)

    Gate 3 (확신 사이징):
        ConvictionSizer.set_conviction() → size_position() 호출.
        Half-Kelly weight가 min_position_pct 미만이면 weight=0 → HOLD.
        통과 시 effective_weight = weight * regime_multiplier.

    참고: Gate 3는 BUY 경로(EXPANSION/RECOVERY)에서만 평가. CONTRACTION/CRISIS는
    Gate 2에서 이미 `action`이 확정되므로 Gate 3을 건너뛴다.

Usage:
    from kis_backtest.portfolio.conviction_sizer import ConvictionSizer
    from kis_backtest.luxon.integration.conviction_bridge import ConvictionBridge

    sizer = ConvictionSizer(max_position_pct=0.20, min_position_pct=0.02)
    bridge = ConvictionBridge(sizer)

    result = await pipeline.checkpoint()
    proposal = bridge.propose(
        checkpoint=result,
        symbol="005930",
        base_conviction=8.0,
        total_capital=100_000_000,
    )
    print(proposal.action, proposal.position_pct, proposal.reason)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kis_backtest.portfolio.macro_regime import Regime

if TYPE_CHECKING:
    from kis_backtest.luxon.integration.phase1_pipeline import (
        Phase1CheckpointResult,
    )
    from kis_backtest.portfolio.conviction_sizer import ConvictionSizer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action constants — 자유 문자열 대신 고정 상수로 감사 로그 일관성 보장
# ---------------------------------------------------------------------------

ACTION_BUY = "BUY"
ACTION_HOLD = "HOLD"
ACTION_REDUCE = "REDUCE"
ACTION_SELL = "SELL"

# 레짐별 포지션 배수 (Druckenmiller 회복기 매수 철학 반영)
_REGIME_MULTIPLIER: dict[Regime, float] = {
    Regime.EXPANSION: 1.0,   # 확장기: 완전 노출
    Regime.RECOVERY: 0.8,    # 회복기: 보수적 매수
    Regime.CONTRACTION: 0.0,  # 수축기: 신규 매수 차단 (기존 포지션 리듀스)
    Regime.CRISIS: 0.0,      # 위기: 완전 청산
}


@dataclass(frozen=True)
class OrderProposal:
    """Dry-run 주문 제안. 3중 게이트 통과/탈락 기록을 포함한 감사 가능 스냅샷.

    Attributes:
        symbol: 종목 코드 (005930 등).
        action: BUY / HOLD / REDUCE / SELL 중 하나.
        position_pct: 최종 포트폴리오 비중 (0.0 ~ max_position_pct).
            HOLD/REDUCE/SELL은 0.0, BUY만 양수.
        conviction: 최종 확신도 (1-10, ConvictionSizer 계산 결과).
            Gate 2 탈락 시에는 입력 base_conviction 그대로.
        reason: 왜 이 결정을 내렸는지 (감사 로그용).
        passed_gates: 통과한 게이트 목록. 순서 보존 tuple.
            예: ("gate1", "gate2:expansion", "gate3")
                ("gate1:partial", "gate2:crisis")
    """
    symbol: str
    action: str
    position_pct: float
    conviction: float
    reason: str
    passed_gates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_actionable(self) -> bool:
        """BUY 또는 SELL처럼 즉시 실행 가능한 제안인가?"""
        return self.action in (ACTION_BUY, ACTION_SELL)


class ConvictionBridge:
    """3중 게이트 어댑터: Phase1CheckpointResult → OrderProposal.

    ConvictionSizer를 얇게 래핑하며, 내부 state를 매 propose() 호출마다 갱신한다.
    stateless 래퍼는 아니다(set_conviction이 내부 dict에 저장) — 하지만 동일
    symbol을 여러 번 propose()해도 항상 최신 base_conviction으로 덮어쓰므로 안전.

    Args:
        sizer: 외부에서 주입된 ConvictionSizer. 수정하지 않고 호출만 한다.
    """

    def __init__(self, sizer: "ConvictionSizer") -> None:
        self._sizer = sizer

    @property
    def sizer(self) -> "ConvictionSizer":
        return self._sizer

    def propose(
        self,
        checkpoint: "Phase1CheckpointResult",
        symbol: str,
        base_conviction: float,
        total_capital: float,
    ) -> OrderProposal:
        """3중 게이트 통과 여부에 따른 주문 제안.

        이 메서드는 **절대 raise 하지 않는다**. 모든 실패는 HOLD + reason으로
        변환한다. 단 total_capital <= 0 같은 명백한 caller 실수는 ValueError
        허용 — 이건 게이트 문제가 아니라 프로그래밍 에러.

        Args:
            checkpoint: Phase1Pipeline.checkpoint() 결과.
            symbol: 매수/매도 대상 종목 코드.
            base_conviction: CUFA 보고서 또는 수동 확신도 (1-10).
            total_capital: 총 투자 자본 (KRW).

        Returns:
            OrderProposal (항상 반환).
        """
        if total_capital <= 0:
            raise ValueError(
                f"total_capital must be positive: {total_capital}"
            )

        passed: list[str] = []

        # ── Gate 1: 파이프라인 건강도 ──────────────────────────
        if not checkpoint.success and not checkpoint.partial_success:
            reason = (
                f"gate1: pipeline dead — "
                f"errors={len(checkpoint.errors)}, fred=0, regime=None"
            )
            logger.info("ConvictionBridge HOLD %s: %s", symbol, reason)
            return OrderProposal(
                symbol=symbol,
                action=ACTION_HOLD,
                position_pct=0.0,
                conviction=base_conviction,
                reason=reason,
                passed_gates=tuple(passed),
            )

        if checkpoint.success:
            passed.append("gate1")
        else:
            # errors가 있지만 일부 컴포넌트는 살아있음
            passed.append("gate1:partial")

        # ── Gate 2: 매크로 레짐 매핑 ───────────────────────────
        regime_result = checkpoint.regime_result
        if regime_result is None:
            reason = (
                f"gate2: regime unknown — "
                f"classify_regime() returned None (indicator_count="
                f"{checkpoint.macro_indicator_count})"
            )
            logger.info("ConvictionBridge HOLD %s: %s", symbol, reason)
            return OrderProposal(
                symbol=symbol,
                action=ACTION_HOLD,
                position_pct=0.0,
                conviction=base_conviction,
                reason=reason,
                passed_gates=tuple(passed),
            )

        regime = regime_result.regime

        # CRISIS: 즉시 청산 시그널
        if regime is Regime.CRISIS:
            passed.append("gate2:crisis")
            reason = (
                f"gate2: CRISIS regime detected "
                f"(confidence={regime_result.confidence:.0%}, "
                f"score={regime_result.score:+.1f}) — liquidate signal"
            )
            logger.warning("ConvictionBridge SELL %s: %s", symbol, reason)
            return OrderProposal(
                symbol=symbol,
                action=ACTION_SELL,
                position_pct=0.0,
                conviction=base_conviction,
                reason=reason,
                passed_gates=tuple(passed),
            )

        # CONTRACTION: 신규 매수 차단
        if regime is Regime.CONTRACTION:
            passed.append("gate2:contraction")
            reason = (
                f"gate2: CONTRACTION regime "
                f"(confidence={regime_result.confidence:.0%}, "
                f"score={regime_result.score:+.1f}) — "
                f"no new entries, reduce existing"
            )
            logger.info("ConvictionBridge REDUCE %s: %s", symbol, reason)
            return OrderProposal(
                symbol=symbol,
                action=ACTION_REDUCE,
                position_pct=0.0,
                conviction=base_conviction,
                reason=reason,
                passed_gates=tuple(passed),
            )

        # EXPANSION or RECOVERY → BUY 경로로 진행
        regime_mult = _REGIME_MULTIPLIER.get(regime, 0.0)
        if regime_mult <= 0.0:
            # 방어: 미래에 Regime enum이 확장될 경우 알 수 없는 값은 HOLD로 안전
            reason = (
                f"gate2: unmapped regime={regime.value} — defaulting to HOLD"
            )
            logger.warning("ConvictionBridge HOLD %s: %s", symbol, reason)
            return OrderProposal(
                symbol=symbol,
                action=ACTION_HOLD,
                position_pct=0.0,
                conviction=base_conviction,
                reason=reason,
                passed_gates=tuple(passed),
            )

        passed.append(f"gate2:{regime.value}")

        # ── Gate 3: 확신 사이징 ────────────────────────────────
        level = self._sizer.set_conviction(
            symbol=symbol,
            base_conviction=base_conviction,
        )
        position = self._sizer.size_position(
            symbol=symbol,
            total_capital=total_capital,
        )

        if position.weight <= 0.0:
            reason = (
                f"gate3: conviction too low — "
                f"final={level.final_conviction:.2f}, "
                f"kelly_raw={position.kelly_raw:.4f} < "
                f"min={self._sizer.min_position_pct:.4f}"
            )
            logger.info("ConvictionBridge HOLD %s: %s", symbol, reason)
            return OrderProposal(
                symbol=symbol,
                action=ACTION_HOLD,
                position_pct=0.0,
                conviction=level.final_conviction,
                reason=reason,
                passed_gates=tuple(passed),
            )

        passed.append("gate3")
        effective_weight = round(position.weight * regime_mult, 6)
        reason = (
            f"all gates passed — regime={regime.value} "
            f"(mult={regime_mult:.2f}), "
            f"conviction={level.final_conviction:.2f}, "
            f"kelly={position.kelly_raw:.4f}, "
            f"weight={position.weight:.4f} → "
            f"effective={effective_weight:.4f}"
        )
        logger.info("ConvictionBridge BUY %s: %s", symbol, reason)
        return OrderProposal(
            symbol=symbol,
            action=ACTION_BUY,
            position_pct=effective_weight,
            conviction=level.final_conviction,
            reason=reason,
            passed_gates=tuple(passed),
        )


__all__ = [
    "ConvictionBridge",
    "OrderProposal",
    "ACTION_BUY",
    "ACTION_HOLD",
    "ACTION_REDUCE",
    "ACTION_SELL",
]
